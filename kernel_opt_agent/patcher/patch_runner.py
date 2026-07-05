from __future__ import annotations

import hashlib
import py_compile
from dataclasses import replace
from pathlib import Path
from typing import Any

from kernel_opt_agent.benchmark.parser import parse_benchmark

from .patch_generator import apply_validated_patch
from .patch_trial import PatchTrial
from .patch_validator import PatchProposal, validate_patch_proposal
from .rollback import rollback_file


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _command_artifact(result: Any) -> dict[str, Any]:
    return {
        "name": getattr(result, "name", None),
        "command": getattr(result, "command", None),
        "returncode": getattr(result, "returncode", None),
        "stdout": getattr(result, "stdout", ""),
        "stderr": getattr(result, "stderr", ""),
        "duration": getattr(result, "duration", None),
        "timeout": getattr(result, "timeout", False),
        "guard_denied": getattr(result, "guard_denied", False),
        "error_message": getattr(result, "error_message", None),
    }


def _with_status(trial: PatchTrial, status: str, error: str | None = None, **updates: Any) -> PatchTrial:
    return replace(trial, status=status, error=error, **updates)


def _rollback(trial: PatchTrial, applied: Any, original_hash: str, target_path: Path, artifacts: dict[str, Any]) -> PatchTrial:
    try:
        rollback_file(applied.rollback)
        restored_hash = _sha256(target_path)
        artifacts["rollback"] = {
            "record": applied.rollback.to_dict(),
            "restored_sha256": restored_hash,
            "verified": restored_hash == original_hash,
        }
        if restored_hash != original_hash:
            return _with_status(trial, "rolled_back", "rollback verification failed", artifacts=artifacts)
        return replace(trial, artifacts=artifacts)
    except Exception as exc:
        artifacts["rollback"] = {"verified": False, "error": str(exc)}
        return _with_status(trial, "rolled_back", f"{trial.error}; rollback failed: {exc}", artifacts=artifacts)


def _run_command(runner: Any, name: str, command: str | None, artifacts: dict[str, Any]) -> Any:
    result = runner.run(name, command)
    artifacts[name] = _command_artifact(result)
    return result


def _latency(metrics: dict[str, Any]) -> float | None:
    value = metrics.get("latency_ms", metrics.get("latency"))
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def run_patch_trial(
    trial: PatchTrial,
    target_path: Path,
    workspace_root: Path,
    proposal: PatchProposal,
    build_command: str | None,
    correctness_command: str,
    benchmark_command: str,
    runner: Any,
    timeout_seconds: int,
) -> PatchTrial:
    del timeout_seconds
    artifacts = dict(trial.artifacts)
    target = target_path.resolve()
    workspace = workspace_root.resolve()
    if not _inside(target, workspace):
        return _with_status(trial, "validation_failed", f"target_path must be inside workspace_root: {target}", rollback_available=False, artifacts=artifacts)
    try:
        source_text = target.read_text(encoding="utf-8")
        validation = validate_patch_proposal(source_text, proposal, {proposal.target_region})
        artifacts["validation"] = validation.to_dict()
        if not validation.ok:
            return _with_status(trial, "validation_failed", "; ".join(validation.errors), rollback_available=False, artifacts=artifacts)
        original_hash = _sha256(target)
        applied = apply_validated_patch(target, proposal, workspace / "patch_backups", workspace, {proposal.target_region}, allowed_target=target)
        artifacts["applied_patch"] = applied.to_dict()
        trial = replace(trial, status="applied", artifacts=artifacts, error=None)
    except Exception as exc:
        return _with_status(trial, "validation_failed", str(exc), rollback_available=False, artifacts=artifacts)

    try:
        py_compile.compile(str(target), doraise=True)
        artifacts["syntax_check"] = {"ok": True, "target_path": str(target)}
    except py_compile.PyCompileError as exc:
        failed = _with_status(trial, "syntax_failed", str(exc), artifacts=artifacts)
        return _rollback(failed, applied, original_hash, target, artifacts)

    if build_command:
        build = _run_command(runner, "build", build_command, artifacts)
        if build.returncode != 0:
            failed = _with_status(trial, "build_failed", build.error_message or build.stderr or "build command failed", artifacts=artifacts)
            return _rollback(failed, applied, original_hash, target, artifacts)

    correctness = _run_command(runner, "correctness", correctness_command, artifacts)
    if correctness.returncode != 0:
        failed = _with_status(
            trial,
            "correctness_failed",
            correctness.error_message or correctness.stderr or "correctness command failed",
            artifacts=artifacts,
        )
        return _rollback(failed, applied, original_hash, target, artifacts)

    benchmark = _run_command(runner, "benchmark", benchmark_command, artifacts)
    if benchmark.returncode != 0:
        return _with_status(trial, "benchmark_failed", benchmark.error_message or benchmark.stderr or "benchmark command failed", artifacts=artifacts)
    parsed = parse_benchmark(benchmark.stdout, {"latency": None, "tflops": None, "bandwidth": None})
    metrics_after = {
        "latency_ms": parsed.latency,
        "tflops": parsed.tflops,
        "bandwidth": parsed.bandwidth,
        "parse_error": parsed.parse_error,
        "reason": parsed.reason,
    }
    artifacts["benchmark_metrics"] = metrics_after
    if parsed.parse_error:
        return replace(trial, status="benchmark_failed", metrics_after=metrics_after, artifacts=artifacts, error=parsed.reason or "benchmark metrics not found")
    before = _latency(trial.metrics_before)
    after = parsed.latency
    improvement = None
    status = "benchmark_ok"
    if before is not None and after is not None:
        improvement = before - after
        if after > before:
            status = "benchmark_regressed"
    return replace(trial, status=status, metrics_after=metrics_after, improvement=improvement, artifacts=artifacts, error=None)
