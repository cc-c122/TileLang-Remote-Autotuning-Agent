from __future__ import annotations

import argparse
import logging
import sys
import time
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
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.kernel.variant_generator import VariantGenerator
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


def run_trial(
    config: AppConfig,
    db: ExperimentDB,
    run_id: str,
    iteration: int,
    candidate_id: int,
    candidate_config: dict[str, Any],
    paths: dict[str, Path],
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

    try:
        runner = build_runner(config, paths["trial_dir"])
        corr_cmd = runner.run("correctness", config.kernel.correctness_command)
        stdout_all.append(corr_cmd.stdout)
        stderr_all.append(corr_cmd.stderr)
        if corr_cmd.returncode != 0 and corr_cmd.guard_denied:
            status = command_failure_status(corr_cmd, "correctness")
            error = {"category": status, "message": corr_cmd.error_message or corr_cmd.stderr}
        else:
            corr = parse_correctness(corr_cmd.stdout, corr_cmd.stderr, corr_cmd.returncode)
            correctness_data = {"passed": corr.passed, "status": corr.status, "max_error": corr.max_error, "reason": corr.reason, "parse_error": corr.parse_error}
            if not corr.passed:
                status = "correctness_failed"
                error = {"category": status, "message": corr.reason}
        if error is None and config.kernel.build_command:
            build = runner.run("build", config.kernel.build_command)
            stdout_all.append(build.stdout)
            stderr_all.append(build.stderr)
            if build.returncode != 0:
                status = command_failure_status(build, "build")
                error = {"category": status, "message": build.error_message or build.stderr[-500:]}
        if error is None:
            bench = runner.run("benchmark", config.kernel.run_command)
            stdout_all.append(bench.stdout)
            stderr_all.append(bench.stderr)
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
    finally:
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

    stdout_path, stderr_path = db.write_logs(label, "\n".join(stdout_all), "\n".join(stderr_all))
    record = {
        "run_id": run_id,
        "iteration": iteration,
        "candidate_id": candidate_id,
        "config": candidate_config,
        "config_hash": config_hash(candidate_config),
        "status": status,
        "correctness": correctness_data,
        "metrics": metrics_data,
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
    db.append(record)
    return record


def run(config: AppConfig) -> None:
    setup_logging()
    if config.runner.type == "ssh" and config.remote.auth_type == "password":
        raise NotImplementedError("SSH password authentication is reserved but not implemented in V1; use remote.auth_type=key")
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
    record = run_trial(config, db, run_id, 0, 0, baseline, paths)
    history.append(record)
    if record["status"] == "benchmark_ok":
        best_value = record["objective"]["value"]

    for iteration in range(1, config.search.max_iterations + 1):
        candidates, source = policy.propose(config.search.strategy, config.search.candidates_per_iteration, tried, history)
        logging.info("iteration %s generated %s candidates via %s", iteration, len(candidates), source)
        if not candidates:
            break
        for candidate_id, cand in enumerate(candidates, start=1):
            h = config_hash(cand)
            if h in tried:
                continue
            tried.add(h)
            try:
                paths = generator.create_trial(iteration, candidate_id, cand)
                record = run_trial(config, db, run_id, iteration, candidate_id, cand, paths)
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

    best = write_final_report(RESULTS_DIR, db.records, config.search.objective, hardware_info)
    logging.info("done; best=%s results=%s", best.get("config_hash") if best else "none", RESULTS_DIR)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        run(config)
    except (NotImplementedError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
