from __future__ import annotations

import base64
import difflib
import json
import math
import os
import re
import shutil
import statistics
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from kernel_opt_agent.benchmark.correctness import STRICT_RE as CORRECTNESS_STRICT_RE
from kernel_opt_agent.benchmark.correctness import parse_correctness
from kernel_opt_agent.benchmark.parser import parse_benchmark
from kernel_opt_agent.config_model import AppConfig, resolve_path
from kernel_opt_agent.main import build_runner, command_failure_status
from kernel_opt_agent.sample_security import copy_safe_sample_contents, normalize_sample_path

from .analyzer import AnalysisResult, CopyLoopTarget, analyze_source, rewrite_copy_loop, sha256_bytes, sha256_file
from .models import SourceOptimizationResult, SourceOptimizationTrial, SourceTrialCorrectness
from .planner import choose_plan


SOURCE_RESULT_FILENAME = "source_optimization.json"
_HASH_RE = re.compile(r"SOURCE_SHA256=([0-9a-f]{64})")


def source_result_path(results_dir: Path) -> Path:
    return results_dir / SOURCE_RESULT_FILENAME


def write_source_result(results_dir: Path, result: SourceOptimizationResult) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    source_result_path(results_dir).write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _tree_hashes(root: Path, excluded: set[str] | None = None) -> dict[str, str]:
    excluded = excluded or set()
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in excluded:
            hashes[relative] = sha256_file(path)
    return hashes


def _assert_within(path: Path, root: Path) -> None:
    path.resolve().relative_to(root.resolve())


def _hash_command(entry_file: str) -> str:
    safe_name = normalize_sample_path(entry_file, label="kernel.entry_file")
    encoded_name = base64.b64encode(safe_name.encode("utf-8")).decode("ascii")
    script = (
        "import base64,hashlib,pathlib;"
        f"p=pathlib.Path(base64.b64decode('{encoded_name}').decode('utf-8'));"
        "print('SOURCE_SHA256='+hashlib.sha256(p.read_bytes()).hexdigest())"
    )
    escaped = script.replace('"', '\\"')
    return f'python -c "{escaped}"'


def _command_error(result: Any, stage: str) -> str:
    return result.error_message or result.stderr[-500:] or command_failure_status(result, stage)


def _redact_text(text: str | None, config: AppConfig) -> str | None:
    if text is None:
        return None
    redacted = text
    for env_name in (config.remote.password_env, config.llm.api_key_env):
        secret = os.environ.get(env_name or "")
        if secret:
            redacted = redacted.replace(secret, "<redacted:secret>")
    if config.remote.key_path:
        redacted = redacted.replace(config.remote.key_path, "<redacted:key_path>")
        redacted = redacted.replace(os.path.expanduser(config.remote.key_path), "<redacted:key_path>")
    return redacted


class _ExecutionOutcome:
    def __init__(self) -> None:
        self.status = "benchmark_ok"
        self.error: str | None = None
        self.correctness = SourceTrialCorrectness()
        self.samples: list[float] = []
        self.execution_hash: str | None = None
        self.logs: dict[str, str] = {}


def _execute_version(
    config: AppConfig,
    trial_dir: Path,
    expected_source_hash: str,
    repeats: int,
    should_cancel: Callable[[], bool],
    event_callback: Callable[[str, str], None] | None,
    label: str,
) -> _ExecutionOutcome:
    outcome = _ExecutionOutcome()
    runner = None
    try:
        if should_cancel():
            outcome.status = "cancelled"
            outcome.error = "cancel requested before candidate execution"
            return outcome
        runner = build_runner(config, trial_dir)
        hash_result = runner.run("source_hash", _hash_command(config.kernel.entry_file))
        outcome.logs["source_hash"] = hash_result.stdout + hash_result.stderr
        match = _HASH_RE.search(hash_result.stdout)
        if hash_result.returncode != 0 or match is None:
            outcome.status = "validation_failed"
            outcome.error = _command_error(hash_result, "source_hash") or "execution source hash unavailable"
            return outcome
        outcome.execution_hash = match.group(1)
        if outcome.execution_hash != expected_source_hash:
            outcome.status = "validation_failed"
            outcome.error = "execution source hash does not match the local candidate"
            return outcome
        if config.kernel.build_command:
            if event_callback:
                event_callback("build_started", f"{label}: running build command")
            build = runner.run("build", config.kernel.build_command)
            outcome.logs["build"] = build.stdout + build.stderr
            if build.returncode != 0:
                outcome.status = "build_failed"
                outcome.error = _command_error(build, "build")
                return outcome
        if should_cancel():
            outcome.status = "cancelled"
            outcome.error = "cancel requested after build"
            return outcome
        post_build_hash = runner.run("source_hash_after_build", _hash_command(config.kernel.entry_file))
        outcome.logs["source_hash_after_build"] = post_build_hash.stdout + post_build_hash.stderr
        match = _HASH_RE.search(post_build_hash.stdout)
        if post_build_hash.returncode != 0 or match is None or match.group(1) != expected_source_hash:
            outcome.status = "validation_failed"
            outcome.error = "build changed the candidate source or its execution hash is unavailable"
            return outcome
        if event_callback:
            event_callback("correctness_started", f"{label}: running strict correctness gate")
        correctness = runner.run("correctness", config.kernel.correctness_command)
        outcome.logs["correctness"] = correctness.stdout + correctness.stderr
        strict_match = CORRECTNESS_STRICT_RE.search(correctness.stdout + "\n" + correctness.stderr)
        parsed_correctness = parse_correctness(correctness.stdout, correctness.stderr, correctness.returncode)
        outcome.correctness = SourceTrialCorrectness(
            passed=parsed_correctness.passed if strict_match is not None else False,
            max_error=parsed_correctness.max_error,
            reason=parsed_correctness.reason if strict_match is not None else "strict CORRECTNESS_RESULT record is required",
        )
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
            benchmark = runner.run(f"benchmark_{index + 1}", config.kernel.run_command)
            outcome.logs[f"benchmark_{index + 1}"] = benchmark.stdout + benchmark.stderr
            if benchmark.returncode != 0:
                outcome.status = "benchmark_failed"
                outcome.error = _command_error(benchmark, "benchmark")
                return outcome
            if "BENCHMARK_RESULT" not in benchmark.stdout:
                outcome.status = "benchmark_failed"
                outcome.error = "strict BENCHMARK_RESULT record is required"
                return outcome
            parsed = parse_benchmark(benchmark.stdout, {"latency": None, "tflops": None, "bandwidth": None})
            if parsed.parse_error or parsed.latency is None or not math.isfinite(parsed.latency) or parsed.latency < 0:
                outcome.status = "benchmark_failed"
                outcome.error = parsed.reason or "benchmark latency is invalid"
                return outcome
            outcome.samples.append(parsed.latency)
        return outcome
    except Exception as exc:
        outcome.status = "validation_failed"
        outcome.error = str(exc)
        return outcome
    finally:
        outcome.error = _redact_text(outcome.error, config)
        outcome.logs = {name: _redact_text(content, config) or "" for name, content in outcome.logs.items()}
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


def _append_report(results_dir: Path, result: SourceOptimizationResult) -> None:
    report_path = results_dir / "report.md"
    prior = report_path.read_text(encoding="utf-8") if report_path.is_file() else "# TileLang Autotuning Report\n"
    lines = [
        "",
        "## Source Optimization",
        f"- Status: {result.status}",
        f"- Reason: {result.reason or 'none'}",
        f"- Baseline source SHA-256: `{result.baseline_source_sha256 or 'unknown'}`",
        f"- Best source SHA-256: `{result.best_source_sha256 or 'unknown'}`",
        f"- Accepted trial: {result.accepted_trial_id or 'none'}",
        "",
        "| Trial | Optimization | Status | Baseline median (ms) | Candidate median (ms) | Improvement | Rollback verified |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    if not result.trials:
        lines.append("| none | none | none |  |  |  |  |")
    for trial in result.trials:
        improvement = "" if trial.improvement_percent is None else f"{trial.improvement_percent:.3f}%"
        lines.append(
            f"| {trial.trial_id} | {trial.optimization_name} | {trial.status} | "
            f"{trial.baseline_latency_ms or ''} | {trial.candidate_latency_ms or ''} | {improvement} | "
            f"{str(trial.rollback_verified).lower()} |"
        )
    lines += [
        "",
        "The source optimizer used a statically authorized structural template. Local fixture tests validate the mechanism only; they are not evidence of target GPU performance.",
    ]
    report_path.write_text(prior.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


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
    baseline_hash = sha256_bytes(original_bytes)
    result = SourceOptimizationResult(
        status="running",
        baseline_source_sha256=baseline_hash,
        best_source_sha256=baseline_hash,
    )
    write_source_result(results_dir, result)
    if not analysis.targets:
        result.status = "unavailable"
        result.reason = analysis.reason
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
                    evidence_rows.append(row)
            except json.JSONDecodeError:
                continue
    evidence_ids = [str(row["evidence_id"]) for row in evidence_rows]
    plan, plan_source = choose_plan(analysis.targets, evidence_ids, planning_client)
    target = next(item for item in analysis.targets if item.target_id == plan.target_id)

    trials_root = workspace / "source_trials"
    _assert_within(trials_root, workspace)
    trials_root.mkdir(parents=True, exist_ok=True)
    baseline_dir = trials_root / "baseline" / "workspace"
    _assert_within(baseline_dir, trials_root)
    if baseline_dir.exists():
        shutil.rmtree(baseline_dir)
    copy_safe_sample_contents(resolve_path(config.kernel.sample_path), baseline_dir)
    baseline_outcome = _execute_version(
        config,
        baseline_dir,
        baseline_hash,
        config.source_optimization.benchmark_repeats,
        cancelled,
        event_callback,
        "source baseline",
    )
    baseline_artifacts = _save_logs(trials_root / "baseline" / "logs", baseline_outcome.logs)
    if baseline_outcome.status != "benchmark_ok":
        result.status = "cancelled" if baseline_outcome.status == "cancelled" else "failed"
        result.reason = f"source optimization baseline failed: {baseline_outcome.status}: {baseline_outcome.error}"
        write_source_result(results_dir, result)
        _append_report(results_dir, result)
        return result
    baseline_median = statistics.median(baseline_outcome.samples)

    trial_id = "source-" + uuid.uuid4().hex[:12]
    candidate_root = trials_root / trial_id
    candidate_dir = candidate_root / "workspace"
    _assert_within(candidate_dir, trials_root)
    copy_safe_sample_contents(resolve_path(config.kernel.sample_path), candidate_dir)
    candidate_entry = candidate_dir.joinpath(*PurePosixPath(normalize_sample_path(config.kernel.entry_file)).parts)
    protected_before = _tree_hashes(candidate_dir, {normalize_sample_path(config.kernel.entry_file)})
    candidate_source = rewrite_copy_loop(original_source, target)
    candidate_entry.write_text(candidate_source, encoding="utf-8", newline="")
    candidate_hash = sha256_file(candidate_entry)
    diff = "".join(
        difflib.unified_diff(
            original_source.splitlines(keepends=True),
            candidate_source.splitlines(keepends=True),
            fromfile=config.kernel.entry_file,
            tofile=config.kernel.entry_file,
        )
    )
    (candidate_root / "source_before.py").write_bytes(original_bytes)
    (candidate_root / "source_after.py").write_text(candidate_source, encoding="utf-8", newline="")
    (candidate_root / "candidate.diff").write_text(diff, encoding="utf-8", newline="")
    trial = SourceOptimizationTrial(
        trial_id=trial_id,
        optimization_name="parallel_copy_to_t_copy",
        target_file=config.kernel.entry_file,
        target_function=target.function_name,
        hypothesis=plan.hypothesis,
        evidence_ids=plan.evidence_ids,
        source_before_sha256=baseline_hash,
        source_after_sha256=candidate_hash,
        status="running",
        diff=diff,
        baseline_latency_ms=baseline_median,
        baseline_samples_ms=baseline_outcome.samples,
        artifacts={"planner": plan_source, "baseline_logs": baseline_artifacts},
    )
    result.trials.append(trial)
    write_source_result(results_dir, result)

    outcome = _execute_version(
        config,
        candidate_dir,
        candidate_hash,
        config.source_optimization.benchmark_repeats,
        cancelled,
        event_callback,
        trial_id,
    )
    trial.execution_source_sha256 = outcome.execution_hash
    trial.artifacts["source_hashes"] = {
        "local_candidate_sha256": candidate_hash,
        "execution_candidate_sha256": outcome.execution_hash,
        "runner_type": config.runner.type,
    }
    trial.correctness = outcome.correctness
    trial.candidate_samples_ms = outcome.samples
    trial.artifacts["candidate_logs"] = _save_logs(candidate_root / "logs", outcome.logs)
    protected_after = _tree_hashes(candidate_dir, {normalize_sample_path(config.kernel.entry_file)})
    protected_changed = any(protected_after.get(path) != digest for path, digest in protected_before.items())
    if protected_changed and outcome.status == "benchmark_ok":
        outcome.status = "validation_failed"
        outcome.error = "a protected dependency or correctness file changed during candidate execution"

    if outcome.status == "benchmark_ok":
        candidate_median = statistics.median(outcome.samples)
        improvement = ((baseline_median - candidate_median) / baseline_median * 100.0) if baseline_median > 0 else None
        trial.candidate_latency_ms = candidate_median
        trial.improvement_percent = improvement
        if improvement is not None and improvement >= config.source_optimization.min_improvement_percent:
            trial.status = "accepted"
            trial.decision_reason = "strict correctness passed and measured median improvement met the configured threshold"
            result.accepted_trial_id = trial_id
            result.best_source_sha256 = candidate_hash
            shutil.copy2(candidate_entry, results_dir / "best_kernel.py")
        else:
            trial.status = "no_improvement"
            trial.decision_reason = "candidate median improvement did not meet the configured threshold"
    else:
        trial.status = outcome.status  # type: ignore[assignment]
        trial.decision_reason = outcome.error

    if trial.status != "accepted":
        candidate_entry.write_bytes(original_bytes)
        trial.rollback_verified = sha256_file(candidate_entry) == baseline_hash
    else:
        trial.rollback_verified = True

    result.status = "cancelled" if trial.status == "cancelled" else "completed"
    result.reason = (
        "candidate accepted"
        if trial.status == "accepted"
        else "no candidate passed all gates and the improvement threshold; baseline retained"
    )
    write_source_result(results_dir, result)
    _append_report(results_dir, result)
    return result
