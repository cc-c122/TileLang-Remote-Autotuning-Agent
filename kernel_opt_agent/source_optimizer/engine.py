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
from kernel_opt_agent.redaction import redact_data, redact_text
from kernel_opt_agent.sample_security import copy_safe_sample_contents, normalize_sample_path

from .analyzer import AnalysisResult, CopyLoopTarget, analyze_source, rewrite_copy_loop, sha256_bytes, sha256_file
from .models import SourceOptimizationResult, SourceOptimizationTrial, SourceTrialCorrectness
from .planner import SourcePlan, choose_plan


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
    return entry, analyze_source(entry.read_text(encoding="utf-8"))


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

    def audit(stage: str) -> bool:
        assert outcome.runner is not None
        ok, error, hashes = _audit_runner(outcome.runner, expected_manifest)
        outcome.stage_hashes[stage] = hashes
        if not ok:
            outcome.status = "validation_failed"
            outcome.error = error
        return ok

    try:
        if should_cancel():
            outcome.status = "cancelled"
            outcome.error = "cancel requested before candidate execution"
            return outcome
        outcome.runner = build_runner(config, trial_dir)
        if not audit("before_build"):
            return outcome
        outcome.execution_hash = outcome.stage_hashes["before_build"].get(entry_name)
        if config.kernel.build_command:
            if event_callback:
                event_callback("build_started", f"{label}: running build command")
            build = outcome.runner.run("build", config.kernel.build_command)
            outcome.logs["build"] = build.stdout + build.stderr
            if not audit("after_build"):
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
        correctness = outcome.runner.run("correctness", config.kernel.correctness_command)
        outcome.logs["correctness"] = correctness.stdout + correctness.stderr
        strict_match = CORRECTNESS_STRICT_RE.search(correctness.stdout + "\n" + correctness.stderr)
        parsed_correctness = parse_correctness(correctness.stdout, correctness.stderr, correctness.returncode)
        outcome.correctness = SourceTrialCorrectness(
            passed=parsed_correctness.passed if strict_match is not None else False,
            max_error=parsed_correctness.max_error,
            reason=parsed_correctness.reason if strict_match is not None else "strict CORRECTNESS_RESULT record is required",
        )
        if not audit("after_correctness"):
            return outcome
        if correctness.returncode != 0 or strict_match is None or not parsed_correctness.passed:
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
            benchmark = outcome.runner.run(f"benchmark_{index + 1}", config.kernel.run_command)
            outcome.logs[f"benchmark_{index + 1}"] = benchmark.stdout + benchmark.stderr
            if not audit(f"after_benchmark_{index + 1}"):
                return outcome
            if benchmark.returncode != 0:
                outcome.status = "benchmark_failed"
                outcome.error = _command_error(benchmark, "benchmark")
                return outcome
            if "BENCHMARK_RESULT" not in benchmark.stdout:
                outcome.status = "benchmark_failed"
                outcome.error = "strict BENCHMARK_RESULT record is required"
                return outcome
            parsed = parse_benchmark(benchmark.stdout, {"latency": None, "tflops": None, "bandwidth": None})
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


def _close_runner(runner: Any | None) -> None:
    if runner is not None and hasattr(runner, "close"):
        try:
            runner.close()
        except Exception:
            pass


def _save_logs(artifact_dir: Path, logs: dict[str, str]) -> dict[str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for name, content in logs.items():
        path = artifact_dir / f"{name}.log"
        path.write_text(content, encoding="utf-8")
        paths[name] = str(path)
    return paths


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
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def _append_report(results_dir: Path, result: SourceOptimizationResult) -> None:
    report_path = results_dir / "report.md"
    prior = report_path.read_text(encoding="utf-8") if report_path.is_file() else "# TileLang Autotuning Report\n"
    if result.accepted_trial_id:
        prior = prior.replace("## Best Seen", "## Pre-source Best Seen", 1)
    accepted = next((item for item in result.trials if item.trial_id == result.accepted_trial_id), None)
    lines = ["", "## Source Optimization", f"- Status: {result.status}", f"- Reason: {result.reason or 'none'}", f"- Baseline source SHA-256: `{result.baseline_source_sha256 or 'unknown'}`", f"- Best source SHA-256: `{result.best_source_sha256 or 'unknown'}`", f"- Accepted trial: {result.accepted_trial_id or 'none'}"]
    if accepted is not None:
        lines += [f"- Authoritative best latency: {accepted.candidate_median_latency_ms} ms", f"- Improvement vs source baseline: {accepted.improvement_percent:.3f}%" if accepted.improvement_percent is not None else "- Improvement vs source baseline: unknown"]
    lines += ["", "| Trial | Optimization | Status | Baseline median (ms) | Candidate median (ms) | Improvement | Rollback verified |", "| --- | --- | --- | ---: | ---: | ---: | --- |"]
    if not result.trials:
        lines.append("| none | none | none |  |  |  |  |")
    for trial in result.trials:
        improvement = "" if trial.improvement_percent is None else f"{trial.improvement_percent:.3f}%"
        rollback = "not_applicable" if trial.rollback_verified is None else str(trial.rollback_verified).lower()
        lines.append(f"| {trial.trial_id} | {trial.optimization_name} | {trial.status} | {trial.baseline_median_latency_ms or ''} | {trial.candidate_median_latency_ms or ''} | {improvement} | {rollback} |")
    lines += ["", "The source optimizer used a statically authorized structural template. Local fixture tests validate the mechanism only; they are not evidence of target GPU performance."]
    report_path.write_text(prior.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def _plans_for_targets(targets: tuple[CopyLoopTarget, ...], evidence_rows: list[dict[str, Any]], max_candidates: int, planning_client: Any | None) -> list[tuple[SourcePlan, str]]:
    first, source = choose_plan(targets, evidence_rows, planning_client)
    plans = [(first, source)]
    selected = {first.target_id}
    evidence_ids = [str(row["evidence_id"]) for row in evidence_rows if row.get("evidence_id")]
    for target in targets:
        if len(plans) >= max_candidates:
            break
        if target.target_id not in selected:
            plans.append((SourcePlan(target_id=target.target_id, hypothesis="replace a verified elementwise copy loop with the TileLang bulk copy primitive", evidence_ids=evidence_ids), "rule_based"))
            selected.add(target.target_id)
    return plans


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
    result = SourceOptimizationResult(status="running", baseline_source_sha256=baseline_hash, best_source_sha256=baseline_hash)
    write_source_result(results_dir, result, config)
    if not analysis.targets:
        result.status = "unavailable"
        result.reason = analysis.reason
        result = _sanitized_result(config, result)
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result

    evidence_rows: list[dict[str, Any]] = []
    evidence_path = results_dir / "metric_observations.jsonl"
    if evidence_path.is_file():
        for line in evidence_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("available") is True and row.get("evidence_id"):
                    evidence_rows.append(_redact_value(config, row))
            except json.JSONDecodeError:
                pass
    plans = _plans_for_targets(analysis.targets, evidence_rows, config.source_optimization.max_candidates, planning_client)

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
    if baseline_outcome.status != "benchmark_ok":
        restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, baseline_outcome.runner, baseline_snapshot, baseline_manifest)
        _close_runner(baseline_outcome.runner)
        baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
        baseline_artifacts["rollback"] = json.dumps({"verified": restored, "error": rollback_error, "hashes": rollback_hashes}, sort_keys=True)
        result.status = "cancelled" if baseline_outcome.status == "cancelled" else "failed"
        result.reason = f"source optimization baseline failed: {baseline_outcome.status}: {baseline_outcome.error}"
        result = _sanitized_result(config, result)
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result
    _close_runner(baseline_outcome.runner)
    baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
    result.baseline_verified = True
    (results_dir / "best_kernel.py").write_bytes(original_bytes)
    if not (results_dir / "best_config.yaml").is_file():
        (results_dir / "best_config.yaml").write_text("{}\n", encoding="utf-8")
    baseline_best_config_bytes = (results_dir / "best_config.yaml").read_bytes()
    baseline_median = statistics.median(baseline_outcome.samples)
    candidates: list[tuple[SourceOptimizationTrial, Path]] = []
    rollback_failed = False

    for plan, plan_source in plans:
        if cancelled():
            break
        target = next(item for item in analysis.targets if item.target_id == plan.target_id)
        trial_id = "source-" + uuid.uuid4().hex[:12]
        candidate_root = trials_root / trial_id
        candidate_dir = candidate_root / "workspace"
        snapshot_dir = candidate_root / "rollback_snapshot"
        _assert_within(candidate_dir, trials_root)
        copy_safe_sample_contents(resolve_path(config.kernel.sample_path), candidate_dir)
        copy_safe_sample_contents(candidate_dir, snapshot_dir)
        baseline_candidate_manifest = _tree_hashes(snapshot_dir)
        candidate_entry = candidate_dir.joinpath(*PurePosixPath(entry_name).parts)
        candidate_source = rewrite_copy_loop(original_source, target)
        candidate_bytes = candidate_source.encode("utf-8")
        candidate_hash = sha256_bytes(candidate_bytes)
        diff = "".join(difflib.unified_diff(original_source.splitlines(keepends=True), candidate_source.splitlines(keepends=True), fromfile=config.kernel.entry_file, tofile=config.kernel.entry_file))
        source_before_path = candidate_root / "source_before.py"
        source_after_path = candidate_root / "source_after.py"
        diff_path = candidate_root / "candidate.diff"
        source_before_path.write_bytes(original_bytes)
        source_after_path.write_bytes(candidate_bytes)
        diff_path.write_text(diff, encoding="utf-8", newline="")
        candidate_entry.write_bytes(candidate_bytes)
        candidate_manifest = dict(baseline_candidate_manifest)
        candidate_manifest[entry_name] = candidate_hash
        trial = SourceOptimizationTrial(
            trial_id=trial_id,
            optimization_name="parallel_copy_to_t_copy",
            target_file=config.kernel.entry_file,
            target_function=target.function_name,
            hypothesis=redact_source_text(config, plan.hypothesis) or "",
            evidence_ids=plan.evidence_ids,
            source_before_sha256=baseline_hash,
            source_after_sha256=candidate_hash,
            status="running",
            diff=diff,
            baseline_latency_ms=list(baseline_outcome.samples),
            baseline_median_latency_ms=baseline_median,
            artifacts={"planner": {"source": plan_source, "attempts": plan.planning_attempts, "fallback_reason": redact_source_text(config, plan.fallback_reason)}, "baseline_logs": baseline_artifacts, "source_before": str(source_before_path), "source_after": str(source_after_path), "diff": str(diff_path), "rollback_snapshot": str(snapshot_dir)},
        )
        result.trials.append(trial)
        write_source_result(results_dir, result, config)
        if event_callback:
            event_callback("source_trial_started", f"{trial_id}: controlled source candidate started")
        outcome = _execute_version(config, candidate_dir, candidate_manifest, config.source_optimization.benchmark_repeats, cancelled, event_callback, trial_id, keep_runner=True)
        restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, outcome.runner, snapshot_dir, baseline_candidate_manifest)
        _close_runner(outcome.runner)
        trial.execution_source_sha256 = outcome.execution_hash
        trial.artifacts["source_hashes"] = {"local_candidate_sha256": candidate_hash, "execution_candidate_sha256": outcome.execution_hash, "runner_type": config.runner.type, "stage_hashes": outcome.stage_hashes}
        trial.correctness = outcome.correctness
        trial.candidate_latency_ms = list(outcome.samples)
        trial.artifacts["candidate_logs"] = _save_logs(candidate_root / "logs", outcome.logs)
        if outcome.samples:
            trial.candidate_median_latency_ms = statistics.median(outcome.samples)
            trial.improvement_percent = ((baseline_median - trial.candidate_median_latency_ms) / baseline_median * 100.0) if baseline_median > 0 else None
        if outcome.status == "benchmark_ok" and trial.improvement_percent is not None and trial.improvement_percent >= config.source_optimization.min_improvement_percent:
            trial.status = "accepted"
            trial.decision_reason = "strict correctness passed and measured median improvement met the configured threshold"
            candidates.append((trial, source_after_path))
        elif outcome.status == "benchmark_ok":
            trial.status = "no_improvement"
            trial.decision_reason = "candidate median improvement did not meet the configured threshold"
        else:
            trial.status = outcome.status  # type: ignore[assignment]
            trial.decision_reason = outcome.error

        trial.rollback_verified = restored
        trial.artifacts["rollback"] = {"verified": restored, "hashes": rollback_hashes, "error": rollback_error}
        if not restored:
            trial.status = "validation_failed"
            trial.decision_reason = f"execution workspace rollback failed: {rollback_error}"
            rollback_failed = True
            candidates = [item for item in candidates if item[0].trial_id != trial.trial_id]
            break

    accepted: SourceOptimizationTrial | None = None
    if cancelled():
        for trial, _ in candidates:
            trial.status = "cancelled"
            trial.decision_reason = "cancel requested after candidate measurement; verified baseline retained"
    if not rollback_failed and candidates and not cancelled():
        accepted, accepted_source_path = min(candidates, key=lambda item: item[0].candidate_median_latency_ms if item[0].candidate_median_latency_ms is not None else float("inf"))
        for trial, _ in candidates:
            if trial is not accepted:
                trial.status = "no_improvement"
                trial.decision_reason = "another verified candidate produced a better median latency"
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
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, trials_root / accepted.trial_id / "rollback_snapshot", baseline_manifest)
            elif deploy_started:
                recovery_runner = None
                try:
                    recovery_runner = build_runner(config, trials_root / accepted.trial_id / "rollback_snapshot")
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
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, trials_root / accepted.trial_id / "rollback_snapshot", baseline_manifest)
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
                restored, rollback_error, rollback_hashes = _restore_execution_workspace(config, publish_runner, trials_root / accepted.trial_id / "rollback_snapshot", baseline_manifest)
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

    if cancelled():
        result.status = "cancelled"
        result.reason = "source optimization cancelled; verified baseline retained"
    elif rollback_failed:
        result.status = "failed"
        result.reason = "source optimization failed an execution workspace restore or publication audit; baseline retained"
        result.accepted_trial_id = None
        result.best_source_sha256 = baseline_hash
    else:
        result.status = "completed"
        result.reason = "candidate accepted" if accepted is not None else "no candidate passed all gates and the improvement threshold; baseline retained"

    for trial in result.trials:
        if event_callback:
            event_callback("source_trial_completed", f"{trial.trial_id}: {trial.status}")
    result = _sanitized_result(config, result)
    _append_trial_records(config, results_dir, result.trials)
    write_source_result(results_dir, result)
    _append_report(results_dir, result)
    return result
