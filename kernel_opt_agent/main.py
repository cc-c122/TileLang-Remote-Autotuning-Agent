from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from kernel_opt_agent.agent.llm_client import OpenAICompatibleClient
from kernel_opt_agent.agent.optimizer_policy import OptimizerPolicy, config_hash
from kernel_opt_agent.agent.planner import Planner
from kernel_opt_agent.benchmark.correctness import parse_correctness
from kernel_opt_agent.benchmark.metrics import is_better
from kernel_opt_agent.benchmark.parser import parse_benchmark
from kernel_opt_agent.config_model import AppConfig, load_config, resolve_path, safe_config_dict
from kernel_opt_agent.diagnosis import (
    EvidenceBundle,
    diagnose_bottlenecks,
    diagnose_from_evidence_records,
    profiler_observations_to_evidence,
)
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.hardware.hardware_info import HardwareInfo
from kernel_opt_agent.kernel.variant_generator import VariantGenerator
from kernel_opt_agent.patcher import PatchProposal, PatchTrial, find_patch_regions, run_patch_trial
from kernel_opt_agent.profiler.base import BaseProfiler, ProfilerResult
from kernel_opt_agent.profiler.dummy_profiler import DummyProfiler
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.profiler.tilelang_log_profiler import TileLangLogProfiler
from kernel_opt_agent.run_request import load_config_from_run_request
from kernel_opt_agent.runner.local_runner import CommandResult, LocalRunner
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner
from kernel_opt_agent.storage.experiment_db import ExperimentDB
from kernel_opt_agent.storage.report_writer import write_final_report


PACKAGE_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PACKAGE_ROOT / "workspace"
RESULTS_DIR = WORKSPACE_ROOT / "results"
GENERATED_DIR = WORKSPACE_ROOT / "generated"
PATCHES_DIR = WORKSPACE_ROOT / "patches"


def setup_logging() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(RESULTS_DIR / "agent.log", encoding="utf-8"),
        ],
    )


def build_runner(config: AppConfig, trial_dir: Path):
    if config.runner.type == "local":
        return LocalRunner(trial_dir, config.search.timeout_seconds, config.constraints.denied_commands)
    info = SSHConnectionInfo(
        host=config.remote.host,
        port=config.remote.port,
        username=config.remote.username,
        auth_type=config.remote.auth_type,
        key_path=config.remote.key_path,
        password_env=config.remote.password_env,
        remote_workspace=config.remote.remote_workspace,
    )
    runner = SSHRunner(info, config.search.timeout_seconds, config.constraints.denied_commands)
    runner.connect()
    runner.upload(trial_dir)
    return runner


def command_failure_status(result: CommandResult, prefix: str) -> str:
    if result.guard_denied:
        return "guard_denied"
    if result.timeout:
        return f"{prefix}_timeout"
    return f"{prefix}_failed"


def runner_exception_category(config: AppConfig, message: str) -> str:
    lowered = message.lower()
    if config.runner.type == "ssh":
        if any(token in lowered for token in ("timed out", "connection", "authentication", "unable to connect")):
            return "ssh_connection_failed"
        if "sftp" in lowered or "upload" in lowered:
            return "sftp_upload_failed"
    return "runner_exception"


def build_profiler(profiler_type: str) -> BaseProfiler:
    if profiler_type == "dummy":
        return DummyProfiler()
    if profiler_type == "tilelang_log":
        return TileLangLogProfiler()
    if profiler_type == "mxmaca":
        return MxmacaProfiler()
    raise ValueError(f"unsupported profiler.type: {profiler_type}")


def collect_profiler_result(
    config: AppConfig,
    paths: dict[str, Path],
    benchmark_stdout: str,
    benchmark_stderr: str,
    compile_log: str,
) -> tuple[dict[str, Any], ProfilerResult]:
    if not config.profiler.enabled:
        result = ProfilerResult.empty()
        return {"enabled": False, "type": config.profiler.type, "result": result.to_dict(), "error": None}, result
    generated_code = ""
    try:
        if paths.get("kernel") and paths["kernel"].exists():
            generated_code = paths["kernel"].read_text(encoding="utf-8")
        profiler = build_profiler(config.profiler.type)
        result = profiler.collect(
            {
                "benchmark_stdout": benchmark_stdout,
                "benchmark_stderr": benchmark_stderr,
                "compile_log": compile_log,
                "generated_code": generated_code,
            }
        )
        return {"enabled": True, "type": config.profiler.type, "result": result.to_dict(), "error": None}, result
    except Exception as exc:
        result = ProfilerResult.empty()
        return {"enabled": True, "type": config.profiler.type, "result": result.to_dict(), "error": str(exc)}, result


def hardware_fields(hardware_info: HardwareInfo | None) -> dict[str, Any]:
    if hardware_info is None:
        return {}
    return hardware_info.to_dict().get("fields", {})


def _metrics_for_patch_trial(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "latency_ms": metrics.get("latency"),
        "tflops": metrics.get("tflops"),
        "bandwidth": metrics.get("bandwidth"),
    }


def _controlled_patch_proposal(target_path: Path) -> tuple[PatchProposal | None, str | None]:
    source_text = target_path.read_text(encoding="utf-8")
    if "BEGIN_AGENT_PATCH" not in source_text:
        return None, "no patch marker"
    try:
        regions = find_patch_regions(source_text)
    except Exception as exc:
        return None, str(exc)
    by_name = {region.name: region for region in regions}
    if "compute" not in by_name:
        return PatchProposal("improve_thread_mapping", "compute", "rule-based fixture", "no expected change", "low", "pass\n"), None
    region = by_name["compute"]
    lines = source_text.splitlines(keepends=True)
    replacement = "".join(lines[region.body_start_line - 1 : region.body_end_line])
    body_lines = replacement.splitlines()
    indent = "    "
    for line in body_lines:
        stripped = line.lstrip()
        if stripped:
            indent = line[: len(line) - len(stripped)]
            break
    replacement = replacement + f"{indent}# controlled_patch_trial: improve_thread_mapping\n"
    return PatchProposal("improve_thread_mapping", "compute", "rule-based fixture", "no expected change", "low", replacement), None


def maybe_run_controlled_patch_trial(
    config: AppConfig,
    db: ExperimentDB,
    record: dict[str, Any],
    paths: dict[str, Path],
    runner: Any,
    metrics_data: dict[str, Any],
) -> None:
    if config.execution_mode == "baseline_only":
        return
    if not (config.patching.enabled and config.patching.run_controlled_trial):
        return
    if db.patch_trials_path.exists() and db.patch_trials_path.read_text(encoding="utf-8").strip():
        return
    target_path = paths.get("kernel")
    trial_dir = paths.get("trial_dir")
    if not target_path or not trial_dir or not target_path.exists():
        return
    proposal, setup_error = _controlled_patch_proposal(target_path)
    if proposal is None:
        if setup_error == "no patch marker":
            return
        trial = PatchTrial(
            trial_id=f"{record['trial_id']}:patch",
            patch_id=f"{record['config_hash']}:patch",
            optimization_name="improve_thread_mapping",
            target_region="compute",
            hypothesis="rule-based fixture",
            expected_improvement="no expected change",
            risk="low",
            status="validation_failed",
            metrics_before=_metrics_for_patch_trial(metrics_data),
            rollback_available=False,
            error=setup_error,
        )
        db.append_patch_trial(trial)
        return
    trial = PatchTrial(
        trial_id=f"{record['trial_id']}:patch",
        patch_id=f"{record['config_hash']}:patch",
        optimization_name=proposal.optimization_name,
        target_region=proposal.target_region,
        hypothesis=proposal.hypothesis,
        expected_improvement=proposal.expected_improvement,
        risk=proposal.risk,
        metrics_before=_metrics_for_patch_trial(metrics_data),
    )
    try:
        result = run_patch_trial(
            trial,
            target_path,
            trial_dir,
            proposal,
            config.kernel.build_command,
            config.kernel.correctness_command,
            config.kernel.run_command,
            runner,
            config.search.timeout_seconds,
        )
    except Exception as exc:
        result = PatchTrial(
            trial_id=trial.trial_id,
            patch_id=trial.patch_id,
            optimization_name=trial.optimization_name,
            target_region=trial.target_region,
            hypothesis=trial.hypothesis,
            expected_improvement=trial.expected_improvement,
            risk=trial.risk,
            status="validation_failed",
            metrics_before=trial.metrics_before,
            rollback_available=False,
            error=str(exc),
        )
    db.append_patch_trial(result)


def run_trial(
    config: AppConfig,
    db: ExperimentDB,
    run_id: str,
    iteration: int,
    candidate_id: int,
    candidate_config: dict[str, Any],
    paths: dict[str, Path],
    hardware_info: HardwareInfo | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    label = f"iter{iteration:03d}_cand{candidate_id:03d}"
    config_path = paths["trial_dir"] / "trial_config.yaml"
    import yaml

    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(candidate_config, f, sort_keys=True)

    started = datetime.utcnow().isoformat() + "Z"
    stdout_all = []
    stderr_all = []
    status = "unknown"
    error: dict[str, str] | None = None
    correctness_data: dict[str, Any] = {"passed": False}
    metrics_data: dict[str, Any] = {"latency": None, "tflops": None, "bandwidth": None}
    objective = {"name": config.search.objective, "value": None, "better": "lower" if config.search.objective == "latency" else "higher"}
    runner = None
    benchmark_stdout = ""
    benchmark_stderr = ""
    compile_log = ""

    try:
        runner = build_runner(config, paths["trial_dir"])
        if config.kernel.build_command:
            if on_event:
                on_event("build_started", "running build command")
            build = runner.run("build", config.kernel.build_command)
            stdout_all.append(build.stdout)
            stderr_all.append(build.stderr)
            compile_log = "\n".join([build.stdout, build.stderr])
            if build.returncode != 0:
                status = command_failure_status(build, "build")
                error = {"category": status, "message": build.error_message or build.stderr[-500:]}
        if error is None:
            if on_event:
                on_event("correctness_started", "running correctness command")
            corr_cmd = runner.run("correctness", config.kernel.correctness_command)
            stdout_all.append(corr_cmd.stdout)
            stderr_all.append(corr_cmd.stderr)
            if corr_cmd.returncode != 0 and corr_cmd.guard_denied:
                status = command_failure_status(corr_cmd, "correctness")
                error = {"category": status, "message": corr_cmd.error_message or corr_cmd.stderr}
            else:
                corr = parse_correctness(corr_cmd.stdout, corr_cmd.stderr, corr_cmd.returncode)
                correctness_data = {
                    "passed": corr.passed,
                    "status": corr.status,
                    "max_error": corr.max_error,
                    "reason": corr.reason,
                    "parse_error": corr.parse_error,
                }
                if not corr.passed:
                    status = "correctness_failed"
                    error = {"category": status, "message": corr.reason}
        if error is None:
            if on_event:
                on_event("benchmark_started", "running benchmark command")
            bench = runner.run("benchmark", config.kernel.run_command)
            stdout_all.append(bench.stdout)
            stderr_all.append(bench.stderr)
            benchmark_stdout = bench.stdout
            benchmark_stderr = bench.stderr
            if bench.returncode != 0:
                status = command_failure_status(bench, "run")
                error = {"category": status, "message": bench.error_message or bench.stderr[-500:]}
            else:
                parsed = parse_benchmark(
                    bench.stdout,
                    {
                        "latency": config.metrics.latency_regex,
                        "tflops": config.metrics.tflops_regex,
                        "bandwidth": config.metrics.bandwidth_regex,
                    },
                )
                metrics_data = {"latency": parsed.latency, "tflops": parsed.tflops, "bandwidth": parsed.bandwidth, "parse_error": parsed.parse_error, "reason": parsed.reason}
                if parsed.parse_error:
                    status = "parse_error"
                    error = {"category": "parse_error", "message": parsed.reason or "benchmark parse failed"}
                else:
                    status = "benchmark_ok"
                    objective["value"] = metrics_data.get(config.search.objective)
    except Exception as exc:
        status = runner_exception_category(config, str(exc))
        error = {"category": status, "message": str(exc)}
        stderr_all.append(str(exc))

    if config.profiler.enabled and on_event:
        on_event("profiling_started", "collecting profiler evidence")
    profiler_record, profiler_result = collect_profiler_result(config, paths, benchmark_stdout, benchmark_stderr, compile_log)
    source_trial_id = f"{run_id}:{label}"
    evidence_records = profiler_observations_to_evidence(profiler_result, source_trial_id)
    metric_observations = [item.to_dict() for item in evidence_records]
    diagnoses = [item.to_dict() for item in diagnose_from_evidence_records(evidence_records)]
    diagnosis = [
        item.to_dict()
        for item in diagnose_bottlenecks(
            EvidenceBundle(
                profiler=profiler_result,
                hardware_fields=hardware_fields(hardware_info),
                source_trial_id=source_trial_id,
                source_metrics=metrics_data,
            )
        )
    ]
    stdout_path, stderr_path = db.write_logs(label, "\n".join(stdout_all), "\n".join(stderr_all))
    record = {
        "run_id": run_id,
        "iteration": iteration,
        "candidate_id": candidate_id,
        "trial_id": source_trial_id,
        "config": candidate_config,
        "config_hash": config_hash(candidate_config),
        "status": status,
        "correctness": correctness_data,
        "metrics": metrics_data,
        "profiler": profiler_record,
        "metric_observations": metric_observations,
        "diagnoses": diagnoses,
        "bottleneck_diagnosis": diagnosis,
        "objective": objective,
        "paths": {
            "kernel": str(paths["kernel"]),
            "kernel_copy": str(paths["kernel_copy"]),
            "patch": str(paths["patch"]),
            "trial_config": str(config_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        },
        "error": error,
        "timestamps": {"started": started, "finished": datetime.utcnow().isoformat() + "Z"},
    }
    if runner is not None:
        maybe_run_controlled_patch_trial(config, db, record, paths, runner, metrics_data)
    if isinstance(runner, SSHRunner):
        try:
            runner.download_artifacts(RESULTS_DIR / "remote_artifacts")
        except Exception as exc:
            stderr_all.append(f"artifact download failed: {exc}")
    if hasattr(runner, "close"):
        try:
            runner.close()
        except Exception:
            pass
    db.append(record)
    return record


def run(
    config: AppConfig,
    should_cancel: Callable[[], bool] | None = None,
    event_callback: Callable[[str, str], None] | None = None,
) -> None:
    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    setup_logging()
    db = ExperimentDB(RESULTS_DIR)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / "effective_config.yaml").write_text(__import__("yaml").safe_dump(safe_config_dict(config), sort_keys=True), encoding="utf-8")
    hardware_info = detect_hardware(config, RESULTS_DIR)
    planner = None
    if config.search.strategy in {"llm", "hybrid"}:
        client = OpenAICompatibleClient(
            config.llm.base_url,
            config.llm.api_key_env,
            config.llm.model,
            config.llm.temperature,
            config.llm.max_tokens,
        )
        planner = Planner(client, config.search_space, hardware_info.safe_probe_results, hardware_info)
    policy = OptimizerPolicy(
        config.search_space,
        config.search.random_seed,
        planner=planner,
        safe_probe_results=hardware_info.safe_probe_results,
        hardware_info=hardware_info,
        conservative_mode=hardware_info.conservative_mode,
    )
    generator = VariantGenerator(
        resolve_path(config.kernel.sample_path),
        config.kernel.entry_file,
        config.search_space,
        GENERATED_DIR,
        PATCHES_DIR,
        config.constraints.allowed_file_patterns,
    )

    tried: set[str] = set()
    history: list[dict[str, Any]] = []
    best_value: float | None = None

    baseline = {k: values[0] for k, values in config.search_space.items()}
    tried.add(config_hash(baseline))
    paths = generator.create_trial(0, 0, baseline)
    logging.info("running baseline")
    if not cancelled():
        record = run_trial(config, db, run_id, 0, 0, baseline, paths, hardware_info, event_callback)
        history.append(record)
        if record["status"] == "benchmark_ok":
            best_value = record["objective"]["value"]

    iterations = range(1, config.search.max_iterations + 1) if config.execution_mode == "parameter_search" else ()
    for iteration in iterations:
        if cancelled():
            logging.info("cancel requested; stopping before iteration %s", iteration)
            break
        candidates, source = policy.propose(config.search.strategy, config.search.candidates_per_iteration, tried, history)
        logging.info("iteration %s generated %s candidates via %s", iteration, len(candidates), source)
        if not candidates:
            break
        for candidate_id, cand in enumerate(candidates, start=1):
            if cancelled():
                logging.info("cancel requested; stopping before candidate %s/%s", iteration, candidate_id)
                break
            h = config_hash(cand)
            if h in tried:
                continue
            tried.add(h)
            try:
                paths = generator.create_trial(iteration, candidate_id, cand)
                record = run_trial(config, db, run_id, iteration, candidate_id, cand, paths, hardware_info, event_callback)
            except Exception as exc:
                record = {
                    "run_id": run_id,
                    "iteration": iteration,
                    "candidate_id": candidate_id,
                    "config": cand,
                    "config_hash": h,
                    "status": "candidate_setup_failed",
                    "correctness": {"passed": False},
                    "metrics": {},
                    "objective": {"name": config.search.objective, "value": None},
                    "paths": {},
                    "error": {"category": "candidate_setup_failed", "message": str(exc)},
                    "timestamps": {"started": datetime.utcnow().isoformat() + "Z", "finished": datetime.utcnow().isoformat() + "Z"},
                }
                db.append(record)
            history.append(record)
            if record["status"] == "benchmark_ok" and is_better(record["objective"]["value"], best_value, config.search.objective):
                best_value = record["objective"]["value"]

    best = write_final_report(
        RESULTS_DIR,
        db.records,
        config.search.objective,
        hardware_info,
        execution_mode=config.execution_mode,
    )
    logging.info("done; best=%s results=%s", best.get("config_hash") if best else "none", RESULTS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", help="Path to config.yaml")
    source.add_argument("--run-request", help="Path to V2 run_request.yaml")
    source.add_argument("--serve-api", action="store_true", help="Start local HTTP API bridge")
    parser.add_argument("--settings", help="Path to V2 user settings.yaml; valid with --run-request or --serve-api")
    parser.add_argument("--api-host", default="127.0.0.1", help="Local API bind host")
    parser.add_argument("--api-port", type=int, default=8765, help="Local API bind port")
    args = parser.parse_args()
    try:
        if args.serve_api:
            from kernel_opt_agent.api.server import create_server

            server = create_server(args.api_host, args.api_port, args.settings) if args.settings else create_server(args.api_host, args.api_port)
            print(f"local API listening on http://{args.api_host}:{args.api_port}")
            server.serve_forever()
            return
        if args.settings and not args.run_request:
            raise ValueError("--settings can only be used together with --run-request")
        if args.run_request:
            config = load_config_from_run_request(args.run_request, args.settings) if args.settings else load_config_from_run_request(args.run_request)
        else:
            config = load_config(args.config)
        run(config)
    except (NotImplementedError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
