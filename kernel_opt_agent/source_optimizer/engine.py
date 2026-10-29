from __future__ import annotations

import csv
import difflib
import json
import math
import shutil
import statistics
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from kernel_opt_agent.benchmark.correctness import STRICT_RE as CORRECTNESS_STRICT_RE
from kernel_opt_agent.benchmark.correctness import parse_correctness
from kernel_opt_agent.benchmark.parser import parse_benchmark
from kernel_opt_agent.config_model import AppConfig, resolve_path
from kernel_opt_agent.main import build_runner, command_failure_status
from kernel_opt_agent.profiler.mcprofiler import McProfilerCollectionRequest
from kernel_opt_agent.profiler.registry import collect_mcprofiler_case as collect_remote_mcprofiler_case
from kernel_opt_agent.redaction import configured_secret_values, redact_data, redact_text
from kernel_opt_agent.sample_security import copy_safe_sample_contents, normalize_sample_path
from kernel_opt_agent.file_task import strict_correctness, strict_benchmark

from .analyzer import AnalysisResult, analyze_source, rewrite_source_target, sha256_bytes, sha256_file
from .models import SourceOptimizationResult, SourceOptimizationTrial, SourceTrialCorrectness
from .performance import CodegenEvidence, assess_performance, parse_codegen_evidence
from .planner import SourcePlan, choose_plan
from .rounds import OptimizationRound, append_round, bound_evidence


SOURCE_RESULT_FILENAME = "source_optimization.json"


def source_result_path(results_dir: Path) -> Path:
    return results_dir / SOURCE_RESULT_FILENAME


def redact_source_text(config: AppConfig, text: str | None) -> str | None:
    return redact_text(config, text)


def _redact_value(config: AppConfig, value: Any) -> Any:
    return redact_data(config, value)


def _sanitized_result(config: AppConfig, result: SourceOptimizationResult) -> SourceOptimizationResult:
    return SourceOptimizationResult.model_validate(_redact_value(config, result.model_dump(mode="python")))


def write_source_result(results_dir: Path, result: SourceOptimizationResult, config: AppConfig | None = None) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump(mode="json") if config is None else _sanitized_result(config, result).model_dump(mode="json")
    source_result_path(results_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_source_result(results_dir: Path) -> dict[str, Any]:
    path = source_result_path(results_dir)
    if not path.is_file():
        return SourceOptimizationResult().model_dump(mode="json")
    try:
        return SourceOptimizationResult.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")
    except Exception:
        return SourceOptimizationResult(status="failed", reason="source optimization result could not be parsed").model_dump(mode="json")


def inspect_source_optimization(config: AppConfig) -> tuple[Path | None, AnalysisResult]:
    sample = resolve_path(config.kernel.sample_path)
    entry_name = normalize_sample_path(config.kernel.entry_file, label="kernel.entry_file")
    entry = sample.joinpath(*PurePosixPath(entry_name).parts) if sample.is_dir() else sample
    if not entry.is_file():
        return None, AnalysisResult((), "kernel entry file does not exist")
    return entry, analyze_source(entry.read_bytes().decode("utf-8"))


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_within(path: Path, root: Path) -> None:
    path.resolve().relative_to(root.resolve())


def _runner_file_sha256(runner: Any, relative_path: str) -> str:
    normalized = normalize_sample_path(relative_path, label="execution artifact")
    if hasattr(runner, "file_sha256"):
        return str(runner.file_sha256(normalized))
    runner_workspace = getattr(runner, "workspace", None)
    if runner_workspace is None:
        raise RuntimeError("runner does not support execution artifact hashing")
    root = Path(runner_workspace).resolve()
    path = (root / Path(*PurePosixPath(normalized).parts)).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise RuntimeError(f"execution artifact not found: {normalized}")
    return sha256_file(path)


def _audit_runner(runner: Any, expected: dict[str, str]) -> tuple[bool, str | None, dict[str, str]]:
    actual: dict[str, str] = {}
    try:
        for relative_path, expected_hash in sorted(expected.items()):
            digest = _runner_file_sha256(runner, relative_path)
            actual[relative_path] = digest
            if digest != expected_hash:
                return False, f"execution artifact changed: {relative_path}", actual
    except Exception as exc:
        return False, f"execution artifact audit failed: {type(exc).__name__}: {exc}", actual
    return True, None, actual


def _command_error(result: Any, stage: str) -> str:
    return result.error_message or result.stderr[-500:] or command_failure_status(result, stage)


class _ExecutionOutcome:
    def __init__(self) -> None:
        self.status = "benchmark_ok"
        self.error: str | None = None
        self.correctness = SourceTrialCorrectness()
        self.samples: list[float] = []
        self.execution_hash: str | None = None
        self.logs: dict[str, str] = {}
        self.stage_hashes: dict[str, dict[str, str]] = {}
        self.runner: Any | None = None
        self.codegen = CodegenEvidence()
        self.stages: dict[str, Any] = {}
        self.restore_allowed = True


def _execute_version(
    config: AppConfig,
    trial_dir: Path,
    expected_manifest: dict[str, str],
    repeats: int,
    should_cancel: Callable[[], bool],
    event_callback: Callable[[str, str], None] | None,
    label: str,
    keep_runner: bool = False,
) -> _ExecutionOutcome:
    outcome = _ExecutionOutcome()
    entry_name = normalize_sample_path(config.kernel.entry_file)

    def run_command(name: str, command: str) -> Any:
        assert outcome.runner is not None
        if hasattr(outcome.runner, "run_cancellable"):
            result = outcome.runner.run_cancellable(name, command, should_cancel, timeout_seconds=config.search.timeout_seconds)
        else:
            result = outcome.runner.run(name, command)
        outcome.stages[name] = {"returncode": result.returncode, "timeout": result.timeout, "guard_denied": result.guard_denied}
        if "stop could not be verified" in (result.error_message or ""):
            outcome.restore_allowed = False
        return result

    def audit(stage: str) -> bool:
        assert outcome.runner is not None
        if not outcome.restore_allowed:
            outcome.status = "validation_failed"
            outcome.error = "execution stop unverified; workspace rewrite withheld"
            return False
        ok, error, hashes = _audit_runner(outcome.runner, expected_manifest)
        outcome.stage_hashes[stage] = hashes
        if not ok:
            outcome.status = "validation_failed"
            outcome.error = error
        return ok

    def command_cancelled(command_result: Any, stage: str) -> bool:
        if (command_result.returncode == 130 and not command_result.timeout
                and not command_result.guard_denied and should_cancel()):
            outcome.status = "cancelled"
            outcome.error = f"cancelled during {stage}; command stop verified"
            return True
        return False

    try:
        if should_cancel():
            outcome.status = "cancelled"
            outcome.error = "cancel requested before candidate execution"
            return outcome
        if config.file_task and any(expected_manifest.get(name) != digest for name, digest in config.file_task.protected_files.items()):
            outcome.status = "validation_failed"
            outcome.error = "protected_file_changed: original test/reference hashes no longer match"
            return outcome
        outcome.runner = build_runner(config, trial_dir)
        if not audit("before_build"):
            return outcome
        outcome.execution_hash = outcome.stage_hashes["before_build"].get(entry_name)
        if config.kernel.build_command:
            if event_callback:
                event_callback("build_started", f"{label}: running build command")
            build = run_command("build", config.kernel.build_command)
            outcome.logs["build"] = build.stdout + build.stderr
            outcome.codegen = parse_codegen_evidence(outcome.logs["build"])
            if not audit("after_build"):
                return outcome
            if command_cancelled(build, "build"):
                return outcome
            if build.returncode != 0:
                outcome.status = "build_failed"
                outcome.error = _command_error(build, "build")
                return outcome
        if should_cancel():
            outcome.status = "cancelled"
            outcome.error = "cancel requested after build"
            return outcome
        if event_callback:
            event_callback("correctness_started", f"{label}: running strict correctness gate")
        correctness = run_command("correctness", config.kernel.correctness_command)
        outcome.logs["correctness"] = correctness.stdout + correctness.stderr
        if not audit("after_correctness"):
            return outcome
        if command_cancelled(correctness, "correctness"):
            return outcome
        if correctness.timeout or correctness.guard_denied:
            outcome.status = "correctness_failed"
            outcome.error = f"{command_failure_status(correctness, 'correctness')}: {_command_error(correctness, 'correctness')}"
            outcome.correctness = SourceTrialCorrectness(passed=False, reason=outcome.error)
            return outcome
        strict_valid = CORRECTNESS_STRICT_RE.search(correctness.stdout + "\n" + correctness.stderr) is not None
        if config.file_task:
            parsed_correctness = strict_correctness(correctness.stdout, correctness.stderr, correctness.returncode,
                                                    expected_manifest[entry_name] if config.file_task.attest_entry else None)
            strict_valid = not parsed_correctness.parse_error
        else:
            parsed_correctness = parse_correctness(correctness.stdout, correctness.stderr, correctness.returncode)
        outcome.correctness = SourceTrialCorrectness(
            passed=parsed_correctness.passed if strict_valid else False,
            max_error=parsed_correctness.max_error,
            reason=parsed_correctness.reason if strict_valid or config.file_task else "strict CORRECTNESS_RESULT record is required",
        )
        if correctness.returncode != 0 or not strict_valid or not parsed_correctness.passed:
            outcome.status = "correctness_failed"
            outcome.error = outcome.correctness.reason
            return outcome
        for index in range(repeats):
            if should_cancel():
                outcome.status = "cancelled"
                outcome.error = "cancel requested before benchmark repeat"
                return outcome
            if event_callback:
                event_callback("benchmark_started", f"{label}: benchmark repeat {index + 1}/{repeats}")
            benchmark = run_command(f"benchmark_{index + 1}", config.kernel.run_command)
            outcome.logs[f"benchmark_{index + 1}"] = benchmark.stdout + benchmark.stderr
            if not audit(f"after_benchmark_{index + 1}"):
                return outcome
            if command_cancelled(benchmark, f"benchmark repeat {index + 1}"):
                return outcome
            if benchmark.returncode != 0:
                outcome.status = "benchmark_failed"
                outcome.error = _command_error(benchmark, "benchmark")
                return outcome
            if "BENCHMARK_RESULT" not in benchmark.stdout:
                outcome.status = "benchmark_failed"
                outcome.error = "strict BENCHMARK_RESULT record is required"
                return outcome
            parsed = strict_benchmark(
                benchmark.stdout, expected_manifest[entry_name] if config.file_task.attest_entry else None,
            ) if config.file_task else parse_benchmark(benchmark.stdout, {"latency": None, "tflops": None, "bandwidth": None})
            if parsed.parse_error or parsed.latency is None or not math.isfinite(parsed.latency) or parsed.latency <= 0:
                outcome.status = "benchmark_failed"
                outcome.error = parsed.reason or "benchmark latency is invalid"
                return outcome
            outcome.samples.append(parsed.latency)
        return outcome
    except Exception as exc:
        outcome.status = "validation_failed"
        outcome.error = f"{type(exc).__name__}: {exc}"
        return outcome
    finally:
        outcome.error = redact_source_text(config, outcome.error)
        outcome.correctness.reason = redact_source_text(config, outcome.correctness.reason)
        outcome.logs = {name: redact_source_text(config, content) or "" for name, content in outcome.logs.items()}
        if not keep_runner:
            _close_runner(outcome.runner)


def _restore_execution_workspace(
    config: AppConfig,
    runner: Any,
    snapshot_dir: Path,
    expected_manifest: dict[str, str],
) -> tuple[bool, str | None, dict[str, str]]:
    try:
        if hasattr(runner, "upload"):
            runner.upload(snapshot_dir)
        else:
            runner_workspace = getattr(runner, "workspace", None)
            if runner_workspace is None:
                raise RuntimeError("runner does not support rollback")
            target_root = Path(runner_workspace).resolve()
            for source in sorted(snapshot_dir.rglob("*")):
                if source.is_file():
                    target = (target_root / source.relative_to(snapshot_dir)).resolve()
                    target.relative_to(target_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        return _audit_runner(runner, expected_manifest)
    except Exception as exc:
        return False, redact_source_text(config, f"{type(exc).__name__}: {exc}"), {}


def _close_runner(runner: Any | None) -> str | None:
    if runner is not None and hasattr(runner, "close"):
        try:
            runner.close()
        except Exception as exc:
            return f"runner cleanup failed: {type(exc).__name__}: {exc}"
    return None


def _save_logs(artifact_dir: Path, logs: dict[str, str]) -> dict[str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, content in logs.items():
        path = artifact_dir / f"{name}.log"
        path.write_text(content, encoding="utf-8")
        paths[name] = str(path)
    return paths


def _shape_id(config: AppConfig) -> str | None:
    field = config.hardware.fields.get("shape_id")
    if isinstance(field, dict):
        field = field.get("value")
    return str(field) if field not in (None, "") else None


def _source_binding(config: AppConfig) -> dict[str, Any]:
    from kernel_opt_agent.runner.ssh_runtime import current_ssh_context

    def value(name):
        field = config.hardware.fields.get(name)
        return field.get("value") if isinstance(field, dict) and "value" in field else field

    context = current_ssh_context()
    device = getattr(context, "device", None)
    device_id = device.get("device_id") if isinstance(device, dict) else value("device_id")
    return {
        "device_id": "device_sha256:" + sha256_bytes(str(device_id).encode()) if device_id else None,
        "shape_id": _shape_id(config), "dtype": value("dtype"),
        "kernel_names": list(config.profiler.kernel_names),
        "unknown_fields": [name for name, item in (("device_id", device_id), ("shape_id", _shape_id(config)), ("dtype", value("dtype"))) if item is None],
    }


def _collect_source_profiler(
    config: AppConfig,
    results_dir: Path,
    outcome: _ExecutionOutcome,
    source_trial_id: str,
    source_sha256: str,
    should_cancel: Callable[[], bool],
    event_callback: Callable[[str, str], None] | None,
) -> dict[str, Any] | None:
    if not (
        config.profiler.enabled
        and config.profiler.type == "mxmaca"
        and config.profiler.auto_collect
        and outcome.status == "benchmark_ok"
        and outcome.runner is not None
    ):
        return None
    if event_callback:
        event_callback("profiling_started", f"{source_trial_id}: collecting mcProfiler evidence")
    metrics = tuple(config.profiler.metrics) if config.profiler.metrics else ()
    binding = _source_binding(config)
    request_kwargs: dict[str, Any] = {
        "target_command": config.profiler.command or config.kernel.run_command,
        "case_name": source_trial_id.replace(":", "-")[:96],
        "source_trial_id": source_trial_id,
        "source_sha256": source_sha256,
        "shape_id": _shape_id(config),
        "device_id": binding["device_id"],
        "dtype": binding["dtype"],
        "kernel_names": tuple(config.profiler.kernel_names),
        "per_kernel": True,
        "executable": config.profiler.mcprofiler_executable,
        "server_executable": config.profiler.mcprofiler_server_executable,
        "service_port": config.profiler.service_port,
        "timeout_seconds": config.profiler.timeout_seconds,
    }
    if metrics:
        request_kwargs["metrics"] = metrics
    try:
        collection = collect_remote_mcprofiler_case(
            outcome.runner,
            results_dir,
            McProfilerCollectionRequest(**request_kwargs),
            should_cancel=should_cancel,
            redactor=lambda value: _redact_value(config, value),
            secret_values=tuple(configured_secret_values(config)),
            append_imported_records=True,
        )
        payload = collection.to_dict(lambda value: _redact_value(config, value))
    except Exception as exc:
        payload = _redact_value(
            config,
            {
                "schema_version": "v2.mcprofiler_collection.v1",
                "status": "failed",
                "reason_category": "adapter_exception",
                "message": f"mcProfiler collection adapter failed: {type(exc).__name__}: {exc}",
                "source_trial_id": source_trial_id,
                "source_sha256": source_sha256,
                "shape_id": _shape_id(config),
                "metrics_requested": list(metrics),
                "available_metrics": {},
                "artifact_manifest": [],
                "fallback": "benchmark_log",
                "execution_manifest": {
                    "target_command_sha256": sha256_bytes(request_kwargs["target_command"].encode("utf-8")),
                    "credentials_forwarded_to_vendor": False,
                },
            },
        )
        with (results_dir / "mcprofiler_collection.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    if event_callback and payload.get("status") == "collected":
        event_callback("profiler_parsed", f"{source_trial_id}: mcProfiler Case parsed")
    payload["evidence_binding"] = {**binding, "source_trial_id": source_trial_id, "source_sha256": source_sha256}
    if payload.get("reason_category") == "cleanup_unverified":
        outcome.restore_allowed = False
        outcome.status = "validation_failed"
        outcome.error = "profiler process stop unverified; workspace rewrite withheld"
    return payload


def _append_csv(path: Path, row: dict[str, Any], default_fields: list[str]) -> None:
    fields = default_fields
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as handle:
            existing = next(csv.reader(handle), [])
            if existing:
                fields = existing
    write_header = not path.is_file() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _existing_run_metadata(results_dir: Path) -> tuple[str, int]:
    run_id = "source-optimization"
    max_iteration = 0
    path = results_dir / "experiments.jsonl"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_id = str(row.get("run_id") or run_id)
            try:
                max_iteration = max(max_iteration, int(row.get("iteration") or 0))
            except (TypeError, ValueError):
                pass
    return run_id, max_iteration


def _append_trial_records(config: AppConfig, results_dir: Path, trials: list[SourceOptimizationTrial]) -> None:
    run_id, max_iteration = _existing_run_metadata(results_dir)
    for index, trial in enumerate(trials, start=1):
        accepted = trial.status == "accepted"
        status = "benchmark_ok" if accepted else trial.status
        latency = trial.candidate_median_latency_ms
        source_config = {
            "mode": "source_optimization",
            "optimization_name": trial.optimization_name,
            "target_function": trial.target_function,
            "source_sha256": trial.source_after_sha256,
        }
        record = _redact_value(config, {
            "run_id": run_id,
            "iteration": max_iteration + index,
            "candidate_id": index,
            "trial_id": trial.trial_id,
            "config": source_config,
            "config_hash": trial.source_after_sha256,
            "status": status,
            "correctness": trial.correctness.model_dump(mode="json"),
            "metrics": {"latency": latency, "tflops": None, "bandwidth": None},
            "objective": {"name": "latency", "value": latency, "better": "lower"},
            "profiler": {"enabled": False, "type": "source_trial", "result": {}, "error": None},
            "metric_observations": [],
            "diagnoses": [],
            "bottleneck_diagnosis": [],
            "paths": {"kernel": trial.artifacts.get("source_after"), "kernel_copy": trial.artifacts.get("source_after"), "patch": trial.artifacts.get("diff")},
            "error": None if accepted else {"category": trial.status, "message": trial.decision_reason},
        })
        serialized = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with (results_dir / "experiments.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(serialized)
        if not accepted:
            with (results_dir / "failed_cases.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(serialized)
        summary_row = {
            "run_id": run_id, "iteration": max_iteration + index, "candidate_id": index, "status": status,
            "latency": latency, "tflops": None, "bandwidth": None, "objective_value": latency,
            "config_hash": trial.source_after_sha256, "kernel_path": trial.artifacts.get("source_after"), "patch_path": trial.artifacts.get("diff"),
        }
        _append_csv(results_dir / "summary.csv", summary_row, ["run_id", "iteration", "candidate_id", "status", "latency", "tflops", "bandwidth", "objective_value", "config_hash", "kernel_path", "patch_path"])
        _append_csv(results_dir / "all_results.csv", summary_row, ["iteration", "candidate_id", "status", "latency", "tflops", "bandwidth", "objective_value", "config_hash"])


def _update_best_config(results_dir: Path, trial: SourceOptimizationTrial) -> None:
    path = results_dir / "best_config.yaml"
    payload: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    payload["source_optimization_result"] = {
        "trial_id": trial.trial_id,
        "optimization_name": trial.optimization_name,
        "source_sha256": trial.source_after_sha256,
        "latency_ms": trial.candidate_median_latency_ms,
        "latency_samples_ms": trial.candidate_latency_ms,
        "improvement_percent": trial.improvement_percent,
        "cumulative_improvement_percent": trial.artifacts.get("cumulative_improvement_percent", trial.improvement_percent),
        "performance_gate": trial.artifacts.get("performance_gate"),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def _append_report(results_dir: Path, result: SourceOptimizationResult) -> None:
    report_path = results_dir / "report.md"
    prior = report_path.read_text(encoding="utf-8") if report_path.is_file() else "# TileLang Autotuning Report\n"
    accepted = next((item for item in result.trials if item.trial_id == result.accepted_trial_id), None)
    samples = result.baseline.get("benchmark_samples_ms") or []
    root_median = statistics.median(samples) if samples else (accepted.baseline_median_latency_ms if accepted else None)
    cumulative = ((root_median - accepted.candidate_median_latency_ms) / root_median * 100.0
                  if accepted and root_median and accepted.candidate_median_latency_ms is not None else None)
    if result.accepted_trial_id:
        prior = prior.replace("## Best Seen", "## Pre-source Best Seen", 1)
    if accepted is not None:
        authoritative = [
            "## Authoritative Source Result",
            f"- Accepted trial: {accepted.trial_id}",
            f"- Baseline median latency: {root_median} ms",
            f"- Best median latency: {accepted.candidate_median_latency_ms} ms",
            f"- Improvement: {cumulative:.3f}%" if cumulative is not None else "- Improvement: unknown",
            "- The earlier single-trial result is retained below as pre-source context.",
            "",
        ]
        title, separator, body = prior.partition("\n")
        prior = title + separator + "\n".join(authoritative) + body.lstrip("\n")
    lines = ["", "## Source Optimization", f"- Status: {result.status}", f"- Reason: {result.reason or 'none'}", f"- Baseline source SHA-256: `{result.baseline_source_sha256 or 'unknown'}`", f"- Best source SHA-256: `{result.best_source_sha256 or 'unknown'}`", f"- Accepted trial: {result.accepted_trial_id or 'none'}"]
    if accepted is not None:
        lines += [f"- Authoritative best latency: {accepted.candidate_median_latency_ms} ms", f"- Improvement vs source baseline: {cumulative:.3f}%" if cumulative is not None else "- Improvement vs source baseline: unknown"]
    lines += [f"- Stop reason: {result.stop_reason or result.status}",
              "- Round audit: optimization_rounds.jsonl; round acceptance selects a verified parent, not final publication.",
              "- Unverified profiler attribution remains excluded. Source/log-only plans are low-confidence hypotheses.",
              "", "| Trial | Optimization | Status | Parent median (ms) | Candidate median (ms) | Parent-relative improvement | Rollback verified |", "| --- | --- | --- | ---: | ---: | ---: | --- |"]
    if not result.trials:
        lines.append("| none | none | none |  |  |  |  |")
    for trial in result.trials:
        improvement = "" if trial.improvement_percent is None else f"{trial.improvement_percent:.3f}%"
        rollback = "not_applicable" if trial.rollback_verified is None else str(trial.rollback_verified).lower()
        lines.append(f"| {trial.trial_id} | {trial.optimization_name} | {trial.status} | {trial.baseline_median_latency_ms or ''} | {trial.candidate_median_latency_ms or ''} | {improvement} | {rollback} |")
    for trial in result.trials:
        lines += ["", f"### Analysis: {trial.trial_id}", f"- Hypothesis: {trial.hypothesis}",
                  f"- Rule template: {trial.artifacts.get('template_description', 'unknown')}",
                  f"- Evidence IDs: {trial.evidence_ids}"]
        gate = trial.artifacts.get("performance_gate")
        if isinstance(gate, dict):
            lines += [
                "", f"### Performance Evidence: {trial.trial_id}",
                f"- Decision: {gate.get('decision')}",
                f"- Reason: {gate.get('reason')}",
                f"- Generated source comparison: {gate.get('codegen_status')}",
                f"- Baseline generated source SHA-256: `{gate.get('baseline_codegen_hash') or 'unknown'}`",
                f"- Candidate generated source SHA-256: `{gate.get('candidate_codegen_hash') or 'unknown'}`",
                f"- Baseline recheck samples (ms): {gate.get('baseline_recheck_samples_ms', [])}",
                f"- Conservative observed improvement: {gate.get('conservative_improvement_percent')}%",
                *[f"- Uncertainty: {item}" for item in gate.get("uncertainty", [])],
            ]
    lines += ["", "The source optimizer used a statically authorized structural template. Local fixture tests validate the mechanism only; they are not evidence of target GPU performance."]
    report_path.write_text(prior.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def _record_round(config: AppConfig, results_dir: Path, index: int, parent_hash: str,
                  plan: SourcePlan, plan_source: str, trial: SourceOptimizationTrial | None,
                  excluded: list[dict[str, Any]], stop_reason: str | None = None) -> None:
    analysis = plan.model_dump(include={"bottleneck_type", "confidence", "hypothesis", "expected_effect", "risk", "validation_plan"})
    analysis["basis"] = "eligible_metrics_and_source" if plan.evidence_ids else "source_and_logs_only"
    artifacts = dict(trial.artifacts) if trial is not None else {}
    artifacts.update({"excluded_evidence": excluded, "planner_attempts": plan.planning_attempts,
                      "fallback_reason": plan.fallback_reason, "unsupported_reason": plan.unsupported_reason,
                      "evidence_binding": _source_binding(config)})
    append_round(results_dir, OptimizationRound(
        round_index=index, trial_id=trial.trial_id if trial else f"source-round-{index:03d}-stop",
        parent_source_sha256=parent_hash, candidate_source_sha256=trial.source_after_sha256 if trial else None,
        evidence_ids=list(plan.evidence_ids), analysis=analysis,
        plan={"decision": plan.decision, "target_id": plan.target_id, "template": plan.template, "source": plan_source},
        template_description=artifacts.get("template_description"), diff=trial.diff if trial else "",
        validation={} if trial is None else {
            "status": trial.status, "correctness": trial.correctness.model_dump(mode="json"),
            "benchmark_samples_ms": trial.candidate_latency_ms, "rollback_verified": trial.rollback_verified,
            "stages": artifacts.get("stages", {}), "performance_gate": artifacts.get("performance_gate"),
            "profiler_collection": artifacts.get("profiler_collection"),
        },
        acceptance={"decision": trial.status if trial else plan.decision,
                    "reason": trial.decision_reason if trial else stop_reason,
                    "scope": "validated_parent_selection_not_final_publication"}, artifacts=artifacts,
    ), lambda value: _redact_value(config, value))


def run_source_optimization(
    config: AppConfig,
    workspace: Path,
    results_dir: Path,
    analysis: AnalysisResult,
    entry_path: Path,
    should_cancel: Callable[[], bool] | None = None,
    event_callback: Callable[[str, str], None] | None = None,
    planning_client: Any | None = None,
) -> SourceOptimizationResult:
    cancelled = should_cancel or (lambda: False)
    original_bytes = entry_path.read_bytes()
    original_source = original_bytes.decode("utf-8")
    entry_name = normalize_sample_path(config.kernel.entry_file)
    baseline_hash = sha256_bytes(original_bytes)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "optimization_rounds.jsonl").write_text("", encoding="utf-8")
    result = SourceOptimizationResult(status="running", baseline_source_sha256=baseline_hash, best_source_sha256=baseline_hash)
    write_source_result(results_dir, result, config)
    if not analysis.targets and config.file_task is None:
        result.status, result.reason = "unavailable", analysis.reason
        result = _sanitized_result(config, result)
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result
    trials_root = workspace / "source_trials"
    _assert_within(trials_root, workspace)
    trials_root.mkdir(parents=True, exist_ok=True)
    baseline_dir = trials_root / "baseline" / "workspace"
    _assert_within(baseline_dir, trials_root)
    if baseline_dir.exists():
        shutil.rmtree(baseline_dir)
    copy_safe_sample_contents(resolve_path(config.kernel.sample_path), baseline_dir)
    baseline_snapshot = trials_root / "baseline" / "rollback_snapshot"
    copy_safe_sample_contents(baseline_dir, baseline_snapshot)
    baseline_manifest = _tree_hashes(baseline_dir)
    baseline_outcome = _execute_version(config, baseline_dir, baseline_manifest, config.source_optimization.benchmark_repeats, cancelled, event_callback, "source baseline", keep_runner=True)
    result.baseline = {"source_sha256": baseline_hash, "status": baseline_outcome.status,
                       "correctness": baseline_outcome.correctness.model_dump(mode="json"),
                       "benchmark_samples_ms": list(baseline_outcome.samples), "stages": baseline_outcome.stages}
    if baseline_outcome.status != "benchmark_ok":
        if baseline_outcome.runner is None and baseline_outcome.status == "cancelled":
            try:
                rollback_hashes = _tree_hashes(baseline_dir)
                restored = rollback_hashes == baseline_manifest
                rollback_error = None if restored else "unstarted baseline workspace changed"
            except Exception as exc:
                restored, rollback_error, rollback_hashes = False, f"baseline audit failed: {type(exc).__name__}: {exc}", {}
        elif not baseline_outcome.restore_allowed:
            restored, rollback_error, rollback_hashes = False, "execution stop unverified; restore withheld", {}
        else:
            restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, baseline_outcome.runner, baseline_snapshot, baseline_manifest)
        cleanup_error = _close_runner(baseline_outcome.runner)
        baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
        baseline_artifacts["rollback"] = json.dumps({"verified": restored, "error": rollback_error, "hashes": rollback_hashes}, sort_keys=True)
        result.status = "cancelled" if baseline_outcome.status == "cancelled" and restored and cleanup_error is None else "failed"
        result.reason = f"source optimization baseline {result.status}: {baseline_outcome.status}: {baseline_outcome.error}"
        if not restored:
            result.reason += f"; rollback failed: {rollback_error}"
        if cleanup_error:
            result.reason += f"; {cleanup_error}"
        result.baseline["artifacts"] = baseline_artifacts
        append_round(results_dir, OptimizationRound(
            round_index=0, trial_id="source-baseline", parent_source_sha256=baseline_hash,
            validation=result.baseline, acceptance={"decision": result.status, "reason": result.reason},
        ), lambda value: _redact_value(config, value))
        result = _sanitized_result(config, result)
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result
    baseline_collection = _collect_source_profiler(
        config,
        results_dir,
        baseline_outcome,
        "source-baseline",
        baseline_hash,
        cancelled,
        event_callback,
    )
    if baseline_collection is not None:
        baseline_ok, baseline_error, baseline_hashes = (
            _audit_runner(baseline_outcome.runner, baseline_manifest) if baseline_outcome.restore_allowed
            else (False, baseline_outcome.error, {})
        )
        baseline_outcome.stage_hashes["after_profiler"] = baseline_hashes
        if not baseline_ok:
            baseline_outcome.status = "validation_failed"
            baseline_outcome.error = baseline_error
            restored, rollback_error, rollback_hashes = (
                _restore_execution_workspace(config, baseline_outcome.runner, baseline_snapshot, baseline_manifest)
                if baseline_outcome.restore_allowed else (False, "profiler stop unverified; restore withheld", {})
            )
            _close_runner(baseline_outcome.runner)
            baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
            baseline_artifacts["profiler_collection"] = baseline_collection
            baseline_artifacts["rollback"] = {
                "verified": restored,
                "error": rollback_error,
                "hashes": rollback_hashes,
            }
            result.status = "failed"
            result.reason = f"source optimization baseline profiler audit failed: {baseline_error}"
            result.baseline.update(status="validation_failed", artifacts=baseline_artifacts)
            append_round(results_dir, OptimizationRound(
                round_index=0, trial_id="source-baseline", parent_source_sha256=baseline_hash,
                validation=result.baseline, acceptance={"decision": "failed", "reason": result.reason},
            ), lambda value: _redact_value(config, value))
            result = _sanitized_result(config, result)
            write_source_result(results_dir, result)
            _append_report(results_dir, result)
            return result
    _close_runner(baseline_outcome.runner)
    baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
    if baseline_collection is not None:
        baseline_artifacts["profiler_collection"] = baseline_collection
    result.baseline["artifacts"] = baseline_artifacts
    append_round(results_dir, OptimizationRound(
        round_index=0, trial_id="source-baseline", parent_source_sha256=baseline_hash,
        validation=result.baseline, acceptance={"decision": "baseline_verified", "reason": "correctness and independent benchmark completed"},
    ), lambda value: _redact_value(config, value))
    result.baseline_verified = True
    (results_dir / "best_kernel.py").write_bytes(original_bytes)
    if not (results_dir / "best_config.yaml").is_file():
        (results_dir / "best_config.yaml").write_text("{}\n", encoding="utf-8")
    baseline_best_config_bytes = (results_dir / "best_config.yaml").read_bytes()
    baseline_median = statistics.median(baseline_outcome.samples)
    analysis = analyze_source(original_source)
    if not analysis.targets:
        result.status = "unavailable"
        result.stop_reason = "authorized_targets_unavailable"
        result.reason = analysis.reason
        result = _sanitized_result(config, result)
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result
    candidates: list[tuple[SourceOptimizationTrial, Path]] = []
    rollback_failed = False
    parent_source, parent_hash = original_source, baseline_hash
    parent_trial_id = "source-baseline"
    parent_samples, parent_codegen = list(baseline_outcome.samples), baseline_outcome.codegen
    parent_snapshot = baseline_snapshot
    parent_logs = baseline_outcome.logs
    attempted_targets: set[tuple[str, str]] = set()
    attempted_hashes = {baseline_hash}
    history: list[dict[str, Any]] = []

    for round_index in range(1, config.source_optimization.max_candidates + 1):
        if cancelled():
            break
        current_analysis = analyze_source(parent_source)
        targets = tuple(item for item in current_analysis.targets if (parent_hash, item.target_id) not in attempted_targets)
        evidence_rows, excluded = bound_evidence(results_dir, parent_trial_id, parent_hash, _source_binding(config))
        evidence_warnings = (["available evidence excluded: attribution unverified or current parent binding mismatch"] if excluded else [])
        context = _redact_value(config, {
            "round_index": round_index, "remaining_budget": config.source_optimization.max_candidates - round_index + 1,
            "current_best": {"source_sha256": parent_hash, "trial_id": parent_trial_id,
                             "line_count": len(parent_source.splitlines()),
                             "functions": sorted({item.function_name for item in current_analysis.targets}),
                             "benchmark_samples_ms": parent_samples},
            "history": history, "excluded_evidence_count": len(excluded),
            "excluded_evidence_reasons": sorted({reason for row in excluded for reason in row["reasons"]}),
            "analysis_basis": "eligible_metrics_and_source" if evidence_rows else "source_and_logs_only",
            "build_log_excerpt": parent_logs.get("build", "")[-4000:],
        })
        plan, plan_source = choose_plan(targets, evidence_rows, planning_client, context=context,
                                        redactor=lambda value: _redact_value(config, value))
        if cancelled():
            result.stop_reason = "cancelled"
            stopped = plan.model_copy(update={"decision": "stop", "target_id": None, "template": None})
            _record_round(config, results_dir, round_index, parent_hash, stopped, plan_source, None, excluded, "cancelled during planning; no candidate started")
            break
        if plan.decision != "attempt":
            result.stop_reason = plan.unsupported_reason or ("authorized_targets_exhausted" if not targets else plan.decision)
            _record_round(config, results_dir, round_index, parent_hash, plan, plan_source, None, excluded, result.stop_reason)
            break
        target = next(item for item in targets if item.target_id == plan.target_id)
        attempted_targets.add((parent_hash, target.target_id))
        trial_id = "source-" + uuid.uuid4().hex[:12]
        candidate_root = trials_root / trial_id
        candidate_dir = candidate_root / "workspace"
        snapshot_dir = candidate_root / "rollback_snapshot"
        _assert_within(candidate_dir, trials_root)
        copy_safe_sample_contents(resolve_path(config.kernel.sample_path), candidate_dir)
        candidate_entry = candidate_dir.joinpath(*PurePosixPath(entry_name).parts)
        candidate_entry.write_bytes(parent_source.encode("utf-8"))
        copy_safe_sample_contents(candidate_dir, snapshot_dir)
        baseline_candidate_manifest = _tree_hashes(snapshot_dir)
        try:
            if baseline_candidate_manifest != {**baseline_manifest, entry_name: parent_hash}:
                raise ValueError("protected_file_changed: original sample tests/reference changed between rounds")
            candidate_source = rewrite_source_target(parent_source, target)
            if sha256_bytes(candidate_source.encode("utf-8")) in attempted_hashes:
                raise ValueError("duplicate_source: this exact source version was already attempted")
        except Exception as exc:
            trial = SourceOptimizationTrial(
                trial_id=trial_id, optimization_name=target.template,
                target_file=config.kernel.entry_file, target_function=target.function_name,
                hypothesis=redact_source_text(config, plan.hypothesis) or "",
                evidence_ids=plan.evidence_ids, source_before_sha256=parent_hash,
                status="validation_failed",
                decision_reason=redact_source_text(config, f"source rewrite rejected: {type(exc).__name__}: {exc}"),
                baseline_latency_ms=list(parent_samples), baseline_median_latency_ms=statistics.median(parent_samples),
                artifacts={"validation_stage": "source_rewrite", "baseline_logs": baseline_artifacts,
                           "rollback_snapshot": str(snapshot_dir), "source_was_modified": False,
                           "template_description": target.hypothesis, "round_index": round_index},
            )
            result.trials.append(trial)
            history.append({"trial_id": trial_id, "status": trial.status, "reason": trial.decision_reason,
                            "parent_source_sha256": parent_hash, "hypothesis": trial.hypothesis})
            _record_round(config, results_dir, round_index, parent_hash, plan, plan_source, trial, excluded)
            write_source_result(results_dir, result, config)
            if event_callback:
                event_callback("source_trial_completed", f"{trial_id}: validation_failed: {trial.decision_reason}")
            continue
        candidate_bytes = candidate_source.encode("utf-8")
        candidate_hash = sha256_bytes(candidate_bytes)
        attempted_hashes.add(candidate_hash)
        diff = "".join(difflib.unified_diff(parent_source.splitlines(keepends=True), candidate_source.splitlines(keepends=True), fromfile=config.kernel.entry_file, tofile=config.kernel.entry_file))
        source_before_path = candidate_root / "source_before.py"
        source_after_path = candidate_root / "source_after.py"
        diff_path = candidate_root / "candidate.diff"
        source_before_path.write_bytes(parent_source.encode("utf-8"))
        source_after_path.write_bytes(candidate_bytes)
        diff_path.write_text(diff, encoding="utf-8", newline="")
        candidate_entry.write_bytes(candidate_bytes)
        candidate_manifest = dict(baseline_candidate_manifest)
        candidate_manifest[entry_name] = candidate_hash
        trial = SourceOptimizationTrial(
            trial_id=trial_id,
            optimization_name=target.template,
            target_file=config.kernel.entry_file,
            target_function=target.function_name,
            hypothesis=redact_source_text(config, plan.hypothesis) or "",
            evidence_ids=plan.evidence_ids,
            source_before_sha256=parent_hash,
            source_after_sha256=candidate_hash,
            status="running",
            diff=diff,
            baseline_latency_ms=list(parent_samples),
            baseline_median_latency_ms=statistics.median(parent_samples),
            artifacts={"planner": {"source": plan_source, "attempts": plan.planning_attempts, "fallback_reason": redact_source_text(config, plan.fallback_reason), "confidence": plan.confidence, "evidence_warnings": evidence_warnings, "excluded_evidence_count": len(excluded)}, "baseline_logs": baseline_artifacts, "source_before": str(source_before_path), "source_after": str(source_after_path), "diff": str(diff_path), "rollback_snapshot": str(snapshot_dir), "template_description": target.hypothesis, "round_index": round_index},
        )
        result.trials.append(trial)
        write_source_result(results_dir, result, config)
        if event_callback:
            event_callback("source_trial_started", f"{trial_id}: controlled source candidate started")
        outcome = _execute_version(config, candidate_dir, candidate_manifest, config.source_optimization.benchmark_repeats, cancelled, event_callback, trial_id, keep_runner=True)
        candidate_collection = _collect_source_profiler(
            config,
            results_dir,
            outcome,
            trial_id,
            candidate_hash,
            cancelled,
            event_callback,
        )
        if candidate_collection is not None:
            trial.artifacts["profiler_collection"] = candidate_collection
            candidate_ok, candidate_error, candidate_hashes = (
                _audit_runner(outcome.runner, candidate_manifest) if outcome.restore_allowed
                else (False, outcome.error, {})
            )
            outcome.stage_hashes["after_profiler"] = candidate_hashes
            if not candidate_ok:
                outcome.status = "validation_failed"
                outcome.error = candidate_error
        if not outcome.restore_allowed:
            restored, rollback_error, rollback_hashes = False, "execution stop unverified; restore withheld", {}
        elif outcome.runner is None and outcome.status == "cancelled":
            copy_safe_sample_contents(snapshot_dir, candidate_dir)
            rollback_hashes = _tree_hashes(candidate_dir)
            restored = rollback_hashes == baseline_candidate_manifest
            rollback_error = None if restored else "unstarted candidate local restore failed"
        else:
            restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, outcome.runner, snapshot_dir, baseline_candidate_manifest)
        _close_runner(outcome.runner)
        trial.execution_source_sha256 = outcome.execution_hash
        trial.artifacts["source_hashes"] = {"local_candidate_sha256": candidate_hash, "execution_candidate_sha256": outcome.execution_hash, "runner_type": config.runner.type, "stage_hashes": outcome.stage_hashes}
        trial.correctness = outcome.correctness
        trial.artifacts["stages"] = outcome.stages
        trial.candidate_latency_ms = list(outcome.samples)
        trial.artifacts["candidate_logs"] = _save_logs(candidate_root / "logs", outcome.logs)
        if outcome.samples:
            trial.candidate_median_latency_ms = statistics.median(outcome.samples)
            trial.improvement_percent = (statistics.median(parent_samples) - trial.candidate_median_latency_ms) / statistics.median(parent_samples) * 100.0
            trial.artifacts["cumulative_improvement_percent"] = (baseline_median - trial.candidate_median_latency_ms) / baseline_median * 100.0
        if outcome.status == "benchmark_ok" and restored and not cancelled():
            gate = assess_performance(
                parent_samples, outcome.samples, config.source_optimization.min_improvement_percent,
                parent_codegen, outcome.codegen, require_recheck=False,
            )
            if gate.decision == "accepted":
                recheck_dir = candidate_root / "baseline_recheck" / "workspace"
                copy_safe_sample_contents(parent_snapshot, recheck_dir)
                if event_callback:
                    event_callback("baseline_recheck_started", f"{trial_id}: rerunning verified parent source to check timing drift")
                recheck = _execute_version(
                    config, recheck_dir, baseline_candidate_manifest, config.source_optimization.benchmark_repeats,
                    cancelled, event_callback, f"{trial_id} baseline recheck", keep_runner=True,
                )
                recheck_restored, recheck_error, recheck_hashes = _restore_execution_workspace(
                    config, recheck.runner, parent_snapshot, baseline_candidate_manifest,
                )
                _close_runner(recheck.runner)
                trial.artifacts["baseline_recheck"] = {
                    "status": recheck.status, "source_sha256": recheck.execution_hash,
                    "samples_ms": list(recheck.samples), "correctness": recheck.correctness.model_dump(mode="json"),
                    "logs": _save_logs(candidate_root / "baseline_recheck" / "logs", recheck.logs),
                    "stage_hashes": recheck.stage_hashes,
                    "rollback": {"verified": recheck_restored, "hashes": recheck_hashes, "error": recheck_error},
                }
                gate = assess_performance(
                    parent_samples, outcome.samples, config.source_optimization.min_improvement_percent,
                    parent_codegen, outcome.codegen, recheck.samples,
                )
                if recheck.status != "benchmark_ok" or not recheck_restored:
                    gate.decision = "inconclusive"
                    gate.baseline_recheck_passed = False
                    gate.reason = f"baseline recheck failed: {recheck.status}: {recheck.error or recheck_error}"
                elif recheck.codegen.status == "inconsistent" or (
                    parent_codegen.sha256 and recheck.codegen.sha256 != parent_codegen.sha256
                ):
                    gate.decision = "inconclusive"
                    gate.baseline_recheck_passed = False
                    gate.reason = "original source generated inconsistent code during baseline recheck"
                if not recheck_restored:
                    restored, rollback_error, rollback_hashes = recheck_restored, recheck_error, recheck_hashes
            trial.artifacts["performance_gate"] = gate.to_dict()
            trial.status = "accepted" if gate.decision == "accepted" else "no_improvement"
            trial.decision_reason = gate.reason
            if trial.status == "accepted":
                candidates.append((trial, source_after_path))
        elif cancelled():
            trial.status = "cancelled"
            trial.decision_reason = "cancel requested; verified baseline retained"
        else:
            trial.status = outcome.status  # type: ignore[assignment]
            trial.decision_reason = outcome.error

        if cancelled():
            trial.status = "cancelled"
            trial.decision_reason = "cancel requested; verified baseline retained"
            candidates = [item for item in candidates if item[0].trial_id != trial.trial_id]
        trial.rollback_verified = restored
        trial.artifacts["rollback"] = {"verified": restored, "hashes": rollback_hashes, "error": rollback_error}
        if not restored:
            trial.status = "validation_failed"
            trial.decision_reason = f"execution workspace rollback failed: {rollback_error}"
            rollback_failed = True
            candidates = [item for item in candidates if item[0].trial_id != trial.trial_id]
        write_source_result(results_dir, result, config)
        if event_callback:
            event_callback("source_trial_completed", f"{trial_id}: {trial.status}: {trial.decision_reason}")

        candidate_evidence, _ = bound_evidence(results_dir, trial_id, candidate_hash, _source_binding(config))
        history.append({
            "trial_id": trial_id, "parent_source_sha256": parent_hash, "candidate_source_sha256": candidate_hash,
            "status": trial.status, "reason": trial.decision_reason, "hypothesis": trial.hypothesis,
            "correctness": trial.correctness.model_dump(mode="json"), "benchmark_samples_ms": trial.candidate_latency_ms,
            "performance_gate": trial.artifacts.get("performance_gate"), "eligible_metrics": candidate_evidence,
            "profiler_status": (candidate_collection or {}).get("status", "not_collected"),
        })
        _record_round(config, results_dir, round_index, parent_hash, plan, plan_source, trial, excluded)
        if rollback_failed:
            break
        if trial.status == "accepted":
            parent_source, parent_hash, parent_trial_id = candidate_source, candidate_hash, trial_id
            parent_samples, parent_codegen, parent_logs = list(outcome.samples), outcome.codegen, outcome.logs
            parent_snapshot = candidate_root / "accepted_parent_snapshot"
            copy_safe_sample_contents(snapshot_dir, parent_snapshot)
            parent_snapshot.joinpath(*PurePosixPath(entry_name).parts).write_bytes(candidate_bytes)
    else:
        result.stop_reason = "budget_exhausted"

    accepted: SourceOptimizationTrial | None = None
    if cancelled():
        for trial, _ in candidates:
            trial.status = "cancelled"
            trial.decision_reason = "cancel requested after candidate measurement; verified baseline retained"
        if parent_hash != baseline_hash and not rollback_failed:
            recovery_runner = None
            try:
                recovery_runner = build_runner(config, baseline_snapshot)
                restored, recovery_error, recovery_hashes = _audit_runner(recovery_runner, baseline_manifest)
                result.baseline["cancel_restore"] = {"verified": restored, "hashes": recovery_hashes, "error": recovery_error}
                rollback_failed = rollback_failed or not restored
            except Exception as exc:
                result.baseline["cancel_restore"] = {"verified": False, "exception_type": type(exc).__name__}
                rollback_failed = True
            finally:
                _close_runner(recovery_runner)
    if not rollback_failed and candidates and not cancelled():
        accepted, accepted_source_path = candidates[-1]
        publish_dir = trials_root / "accepted" / "workspace"
        if publish_dir.exists():
            shutil.rmtree(publish_dir)
        copy_safe_sample_contents(resolve_path(config.kernel.sample_path), publish_dir)
        publish_entry = publish_dir.joinpath(*PurePosixPath(entry_name).parts)
        publish_entry.write_bytes(accepted_source_path.read_bytes())
        publish_manifest = dict(baseline_manifest)
        publish_manifest[entry_name] = accepted.source_after_sha256 or ""
        publish_runner = None
        publish_ok = False
        publish_error: str | None = None
        publish_needs_rollback = False
        deploy_started = False
        try:
            if sha256_file(accepted_source_path) != accepted.source_after_sha256:
                publish_error = "accepted source artifact hash changed before publication"
            elif cancelled():
                publish_error = "cancel requested before accepted candidate publication"
            else:
                deploy_started = True
                publish_runner = build_runner(config, publish_dir)
                publish_needs_rollback = True
                publish_ok, publish_error, publish_hashes = _audit_runner(publish_runner, publish_manifest)
                accepted.artifacts["publish_hashes"] = publish_hashes
                if publish_ok and sha256_file(accepted_source_path) != accepted.source_after_sha256:
                    publish_ok = False
                    publish_error = "accepted source artifact hash changed during publication"
                if publish_ok and cancelled():
                    publish_ok = False
                    publish_error = "cancel requested during accepted candidate publication"
        except Exception as exc:
            publish_error = redact_source_text(config, f"{type(exc).__name__}: {exc}")
        if not publish_ok:
            if publish_runner is not None and publish_needs_rollback:
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, baseline_snapshot, baseline_manifest)
            elif deploy_started:
                recovery_runner = None
                try:
                    recovery_runner = build_runner(config, baseline_snapshot)
                    restored, rollback_error, rollback_hashes = _audit_runner(recovery_runner, baseline_manifest)
                except Exception as exc:
                    restored, rollback_error, rollback_hashes = False, redact_source_text(config, f"recovery runner failed: {type(exc).__name__}: {exc}"), {}
                finally:
                    _close_runner(recovery_runner)
            else:
                restored, rollback_error, rollback_hashes = True, None, {}
            accepted.status = "cancelled" if cancelled() else "validation_failed"
            accepted.rollback_verified = restored
            accepted.decision_reason = f"accepted candidate publication audit failed: {publish_error}; rollback: {rollback_error}"
            accepted.artifacts["publish_rollback"] = {"verified": restored, "hashes": rollback_hashes, "error": rollback_error}
            rollback_failed = not restored or not cancelled()
            accepted = None
        else:
            try:
                if cancelled():
                    raise RuntimeError("cancel requested before accepted result publication")
                shutil.copy2(accepted_source_path, results_dir / "best_kernel.py")
                if sha256_file(results_dir / "best_kernel.py") != accepted.source_after_sha256:
                    raise RuntimeError("published best kernel hash does not match accepted source")
                if cancelled():
                    raise RuntimeError("cancel requested during accepted result publication")
                _update_best_config(results_dir, accepted)
                accepted.rollback_verified = None
                result.accepted_trial_id = accepted.trial_id
                result.best_source_sha256 = accepted.source_after_sha256
            except Exception as exc:
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, baseline_snapshot, baseline_manifest)
                accepted.status = "cancelled" if cancelled() else "validation_failed"
                accepted.rollback_verified = restored
                accepted.decision_reason = redact_source_text(config, f"accepted result publication failed: {type(exc).__name__}: {exc}; rollback: {rollback_error}")
                accepted.artifacts["publish_rollback"] = {"verified": restored, "hashes": rollback_hashes, "error": rollback_error}
                rollback_failed = True
                result.accepted_trial_id = None
                result.best_source_sha256 = baseline_hash
                (results_dir / "best_kernel.py").write_bytes(original_bytes)
                (results_dir / "best_config.yaml").write_bytes(baseline_best_config_bytes)
                accepted = None
            if accepted is not None and event_callback:
                try:
                    event_callback("best_updated", f"{accepted.trial_id}: verified source candidate became best")
                except Exception:
                    pass
            if accepted is not None and cancelled():
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, baseline_snapshot, baseline_manifest)
                accepted.status = "cancelled"
                accepted.rollback_verified = restored
                accepted.decision_reason = "cancel requested after candidate publication; verified baseline restored"
                accepted.artifacts["publish_rollback"] = {"verified": restored, "hashes": rollback_hashes, "error": rollback_error}
                result.accepted_trial_id = None
                result.best_source_sha256 = baseline_hash
                (results_dir / "best_kernel.py").write_bytes(original_bytes)
                (results_dir / "best_config.yaml").write_bytes(baseline_best_config_bytes)
                accepted = None
        _close_runner(publish_runner)

    if rollback_failed:
        result.status = "failed"
        result.reason = "source optimization failed an execution workspace restore or publication audit; baseline retained"
        result.accepted_trial_id = None
        result.best_source_sha256 = baseline_hash
    elif cancelled():
        result.status = "cancelled"
        result.reason = "source optimization cancelled; verified baseline retained"
    else:
        result.status = "completed"
        result.reason = ("candidate accepted" if accepted is not None else
                         f"no candidate attempted: {result.stop_reason}; verified baseline retained" if not result.trials else
                         "no candidate passed all gates and the improvement threshold; baseline retained")

    for trial in result.trials:
        if event_callback:
            event_callback("source_trial_completed", f"{trial.trial_id}: {trial.status}")
    result = _sanitized_result(config, result)
    _append_trial_records(config, results_dir, result.trials)
    write_source_result(results_dir, result)
    _append_report(results_dir, result)
    return result
