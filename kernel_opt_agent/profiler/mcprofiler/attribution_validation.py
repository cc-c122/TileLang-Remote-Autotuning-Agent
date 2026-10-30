"""Backend-only, metric-specific calibration and collection identity checks.

JSON flags are audit data, never authority. Only passing checks authorize a
metric; blocked checks also have an audit ID. Artifacts are rehashed on use.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import hmac
import json
import math
from pathlib import Path, PurePosixPath
import re
import secrets
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..identity import COLLECTION_IDENTITY_FIELDS, IDENTITY_KEYS, canonical_identity


SCHEMA_VERSION = "v2.mcprofiler_metric_validation.v1"


@dataclass(frozen=True)
class MmaDefinition:
    tool_version: str
    tool_build: str
    gpu_model: str
    numerator: str
    denominator: str
    unit: str
    counter_scope: str
    reference_url: str
    reference_sha256: str
    repeat_relative_spread_limit: float


# Code-reviewed official definitions only. No inspected C500 version currently
# documents the denominator AND target/process isolation, so none is approved.
_DOCUMENTED_MMA_DEFINITIONS: Mapping[tuple[str, str, str], MmaDefinition] = MappingProxyType({})


@dataclass(frozen=True)
class NativeScopeProof:
    identity: CollectionIdentity
    report_sha256: str
    config_sha256: str
    counter_scope: str
    target_only_window: bool
    background_excluded: bool
    replay_count: int


# A tool/build-specific reader must establish native trace semantics. Generic
# JSON manifests, filenames and caller booleans cannot provide this proof.
_NATIVE_SCOPE_READERS: Mapping[tuple[str, str], Callable[[bytes], NativeScopeProof]] = MappingProxyType({})


@dataclass(frozen=True)
class ControlCapture:
    mode: str
    value: float | None
    source_sha256: str
    generated_sha256: str | None
    mma_instruction_count: int | None
    report_sha256: str
    window_verified: bool = False
    background_excluded: bool | None = None
    collection_check: CollectionCheck | None = None


@dataclass(frozen=True)
class MmaCalibration:
    tool_version: str | None
    tool_build: str | None
    gpu_model: str | None
    captures: tuple[ControlCapture, ...] = ()

    def evaluate(self) -> dict[str, Any]:
        key = (self.tool_version, self.tool_build, self.gpu_model)
        definition = _DOCUMENTED_MMA_DEFINITIONS.get(key)
        reasons: list[str] = []
        if definition is None:
            reasons.append("metric_definition_unconfirmed")
        groups = {mode: [item for item in self.captures if item.mode == mode]
                  for mode in ("idle", "elementwise", "mma")}
        if any(len(items) < 2 for items in groups.values()):
            reasons.append("calibration_repeats_missing")
        response = bool(groups["elementwise"] and groups["mma"] and
                        all(item.value == 0 for item in groups["elementwise"]) and
                        all(_number(item.value) and item.value > 0 for item in groups["mma"]))
        if not response:
            reasons.append("calibration_control_response_unconfirmed")
        for mode, items in groups.items():
            expected = 0 if mode != "mma" else None
            if any(not item.generated_sha256 or item.mma_instruction_count is None or
                   (item.mma_instruction_count != expected if expected is not None else item.mma_instruction_count <= 0)
                   for item in items):
                reasons.append("control_codegen_unverified")
        if not self.captures or not all(item.window_verified for item in self.captures):
            reasons.append("calibration_window_unverified")
        if not self.captures or not all(item.background_excluded is True for item in self.captures):
            reasons.append("background_workload_not_excluded")
        for item in self.captures:
            check = item.collection_check
            if check is None:
                reasons.append("calibration_artifact_provenance_unverified")
                continue
            reasons.extend(_artifact_reasons(check))
            reasons.extend(_semantic_artifact_reasons({"value": item.value}, check, idle=item.mode == "idle"))
            for key in ("tool_version", "tool_build", "gpu_model"):
                if getattr(check.expected, key) != getattr(self, key):
                    reasons.append("calibration_capture_" + key + "_mismatch")
            if item.mode not in groups or check.expected.kernel_name != "attribution_" + item.mode:
                reasons.append("control_target_mismatch")
            if item.source_sha256 != check.expected.source_sha256 or item.generated_sha256 != check.expected.generated_sha256:
                reasons.append("calibration_source_mismatch")
            if check.artifact_hashes.get(check.report_path) != item.report_sha256:
                reasons.append("calibration_report_mismatch")
            raw = _read_artifact(check, check.generated_path)
            ir = raw.decode("utf-8", errors="replace") if raw else ""
            function = re.search(r"define[^\n]*@attribution_" + re.escape(item.mode) + r"\([^\n]*\{(.*?)^\}", ir, re.M | re.S)
            if item.mode == "idle":
                actual_calls = 0
            else:
                actual_calls = len(re.findall(r"\bcall\b[^\n]*@llvm\.mxc\.mma", function.group(1))) if function else None
            if actual_calls != item.mma_instruction_count:
                reasons.append("control_codegen_mismatch")
        spread: dict[str, float | None] = {}
        for mode, items in groups.items():
            values = [item.value for item in items if _number(item.value)]
            if values and any(values):
                spread[mode] = (max(values) - min(values)) / max(abs(value) for value in values)
            else:
                spread[mode] = 0.0 if values else None
            if definition is not None and mode != "idle" and (
                len(values) < 2 or spread[mode] is None or spread[mode] > definition.repeat_relative_spread_limit
            ):
                reasons.append("calibration_repeatability_failed")
        if definition is None:
            reasons.append("calibration_repeatability_unqualified")
        if any(item.value is not None and (not _number(item.value) or item.value != 0) for item in groups["idle"]):
            reasons.append("idle_control_not_quiet")
        return {"schema_version": SCHEMA_VERSION, "metric": "mma_duty",
                "status": "blocked" if reasons else "verified",
                "reason_codes": list(dict.fromkeys(reasons)),
                "response_differentiation_observed": response,
                "relative_repeat_spread": spread,
                "definition": asdict(definition) if definition else None}


@dataclass(frozen=True)
class CollectionIdentity:
    collection_id: str | None = None
    source_trial_id: str | None = None
    source_sha256: str | None = None
    generated_sha256: str | None = None
    device_id: str | None = None
    shape_id: str | None = None
    dtype: str | None = None
    kernel_name: str | None = None
    launch_id: str | None = None
    tool_version: str | None = None
    tool_build: str | None = None
    gpu_model: str | None = None


@dataclass(frozen=True)
class CollectionCheck:
    workspace_root: Path
    expected: CollectionIdentity
    observed: CollectionIdentity
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    report_path: str | None = None
    config_path: str | None = None
    launch_trace_path: str | None = None
    source_path: str | None = None
    generated_path: str | None = None
    counter_scope: str = "unknown"
    window_verified: bool = False
    background_excluded: bool | None = None
    replay_count: int | None = None
    observed_replay_count: int | None = None
    configured_kernels: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Receipt:
    binding: str
    check: CollectionCheck
    result: dict[str, Any]
    calibration: MmaCalibration | None


_RECEIPTS: dict[str, _Receipt] = {}
_LOCK = RLock()
_RECEIPT_KEY = secrets.token_bytes(32)


def _number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _binding(row: Mapping[str, Any]) -> str:
    identity, reasons = canonical_identity(row)
    if reasons:
        raise ValueError("conflicting or invalid evidence identity")
    scope = row.get("collection_scope")
    scope = scope if isinstance(scope, Mapping) else {}
    identity.update(value=row.get("value"), unit=row.get("unit"), available=row.get("available"),
                    collection_scope={key: value for key, value in scope.items() if key not in IDENTITY_KEYS})
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _artifact_reasons(check: CollectionCheck) -> list[str]:
    reasons = []
    root = check.workspace_root.resolve()
    required = (check.report_path, check.config_path, check.launch_trace_path, check.source_path, check.generated_path)
    if not all(path and path in check.artifact_hashes for path in required):
        reasons.append("required_artifact_missing")
    if not check.artifact_hashes:
        reasons.append("artifact_hashes_missing")
    for relative, expected in check.artifact_hashes.items():
        try:
            if not isinstance(relative, str) or "\\" in relative or ":" in relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                raise ValueError()
            path = root / relative
            if check.workspace_root.is_symlink() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if root in parent.parents):
                raise ValueError()
            path.resolve().relative_to(root)
            if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError()
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                reasons.append("report_hash_mismatch" if relative == check.report_path else "artifact_hash_mismatch")
        except (OSError, ValueError, TypeError):
            reasons.append("artifact_unavailable_or_unsafe")
    return reasons


def _read_artifact(check: CollectionCheck, relative: str | None) -> bytes | None:
    if not relative or relative not in check.artifact_hashes or _artifact_reasons(check):
        return None
    return (check.workspace_root.resolve() / relative).read_bytes()


def _semantic_artifact_reasons(row: Mapping[str, Any], check: CollectionCheck, *, idle: bool = False) -> list[str]:
    reasons: list[str] = []
    try:
        raw = _read_artifact(check, check.report_path)
        report = json.loads(raw) if raw else {}
        values = [item for item in report.get("GPU Throughput Statistics", [])
                  if isinstance(item, dict) and item.get("name") == "AP MMA Duty ratio"]
        if len(values) != 1 or values[0].get("data") != row.get("value") or (values[0].get("isError") and not idle):
            reasons.append("native_metric_value_mismatch")
        raw = _read_artifact(check, check.config_path)
        config = json.loads(raw) if raw else {}
        conditions = config.get("conditions") or {}
        if not idle and (conditions.get("kernelnames") != [check.expected.kernel_name] or
                         conditions.get("include") is not True or conditions.get("is_perkernel") is not True):
            reasons.append("native_kernel_filter_unconfirmed")
        # counts in the configuration is a request, not observed replay evidence.
        if conditions.get("counts") != check.replay_count:
            reasons.append("configured_replay_count_mismatch")
        if conditions.get("profile_from_start") is not False:
            reasons.append("native_custom_window_unconfirmed")
    except (OSError, ValueError, TypeError, AttributeError):
        reasons.append("native_artifact_parse_failed")
    for relative, expected in ((check.source_path, check.expected.source_sha256),
                               (check.generated_path, check.expected.generated_sha256)):
        if not relative or check.artifact_hashes.get(relative) != expected:
            reasons.append("native_source_artifact_mismatch")
    reader = _NATIVE_SCOPE_READERS.get((check.expected.tool_version, check.expected.tool_build))
    raw = _read_artifact(check, check.launch_trace_path)
    if reader is None:
        reasons.append("native_scope_verifier_unavailable")
    elif raw is None:
        reasons.append("native_launch_trace_missing")
    else:
        try:
            proof = reader(raw)
            if not isinstance(proof, NativeScopeProof):
                raise ValueError()
            for key, value in asdict(check.expected).items():
                if value is None or value != getattr(proof.identity, key):
                    reasons.append("native_" + key + "_mismatch")
            if proof.report_sha256 != check.artifact_hashes.get(check.report_path):
                reasons.append("native_report_hash_mismatch")
            if proof.config_sha256 != check.artifact_hashes.get(check.config_path):
                reasons.append("native_config_hash_mismatch")
            if proof.counter_scope != "kernel":
                reasons.append("native_counter_scope_unconfirmed")
            if proof.target_only_window is not True:
                reasons.append("native_collection_window_unverified")
            if proof.background_excluded is not True:
                reasons.append("native_background_workload_not_excluded")
            if proof.replay_count != check.replay_count or proof.replay_count != check.observed_replay_count:
                reasons.append("native_replay_count_mismatch")
        except Exception:
            reasons.append("native_launch_trace_invalid")
    return reasons


def validate_mma_collection(row: Mapping[str, Any], check: CollectionCheck,
                            calibration: MmaCalibration | None = None) -> dict[str, Any]:
    """Called by the live backend collector, never from request/manifest flags."""
    row_identity, identity_reasons = canonical_identity(row)
    if identity_reasons:
        return {"schema_version": SCHEMA_VERSION, "metric": "mma_duty", "status": "blocked",
                "reason_codes": identity_reasons}
    reasons = _artifact_reasons(check)
    reasons.extend(_semantic_artifact_reasons(row, check))
    metric = row_identity["metric"]
    if metric != "mma_duty" or row.get("source") != "mcprofiler":
        reasons.append("metric_not_supported")
    if row.get("unit") != "percent":
        reasons.append("metric_unit_mismatch")
    if row.get("available") is not True or not _number(row.get("value")):
        reasons.append("metric_unavailable_or_non_numeric")
    expected, observed = asdict(check.expected), asdict(check.observed)
    for key, value in expected.items():
        if value is None or observed.get(key) is None:
            reasons.append(key + "_unconfirmed")
        elif value != observed[key]:
            reasons.append(key + "_mismatch")
    required = {"kernel_name", "device_id", "tool_version", "source_sha256", "shape_id", "dtype", "source_trial_id"}
    for key in COLLECTION_IDENTITY_FIELDS:
        value = row_identity[key]
        if value is None and key not in required:
            continue
        if value is None or value != expected.get(key):
            reasons.append("observation_" + key + "_mismatch")
    if check.counter_scope != "kernel":
        reasons.append("counter_scope_unknown")
    if not check.window_verified:
        reasons.append("collection_window_unverified")
    if check.background_excluded is not True:
        reasons.append("background_workload_not_excluded")
    if check.replay_count is None or check.observed_replay_count is None:
        reasons.append("replay_count_unconfirmed")
    elif check.replay_count != check.observed_replay_count:
        reasons.append("replay_count_mismatch")
    if check.configured_kernels != (check.expected.kernel_name,):
        reasons.append("kernel_filter_mismatch")
    calibration_result = calibration.evaluate() if calibration else None
    if calibration_result is None:
        reasons.append("calibration_missing")
        reasons.append("metric_definition_unconfirmed")
    else:
        for key in ("tool_version", "tool_build", "gpu_model"):
            if getattr(calibration, key) != getattr(check.expected, key):
                reasons.append("calibration_" + key + "_mismatch")
        reasons.extend(calibration_result["reason_codes"])
        definition = calibration_result["definition"]
        if definition and (not definition["numerator"] or not definition["denominator"] or
                           definition["counter_scope"] != "kernel" or definition["unit"] != row.get("unit") or
                           not re.fullmatch(r"[0-9a-f]{64}", definition["reference_sha256"])):
            reasons.append("metric_definition_invalid")
    result = {"schema_version": SCHEMA_VERSION, "metric": "mma_duty",
              "status": "blocked" if reasons else "verified",
              "reason_codes": list(dict.fromkeys(reasons)),
              "collection_id": check.expected.collection_id if re.fullmatch(r"[A-Za-z0-9_-]{1,96}", check.expected.collection_id or "") else None,
              "definition": calibration_result["definition"] if calibration_result else None}
    try:
        binding = _binding(row)
        digest = hmac.new(_RECEIPT_KEY, (binding + json.dumps(result, sort_keys=True)).encode(), hashlib.sha256).hexdigest()
    except (TypeError, ValueError, OverflowError):
        return {**result, "status": "blocked", "reason_codes": [*result["reason_codes"], "observation_malformed"]}
    validation_id = "mcv_" + digest
    result["validation_id"] = validation_id
    # Copy the input containers so later caller mutation cannot alter a receipt.
    frozen_check = replace(check, artifact_hashes=MappingProxyType(dict(check.artifact_hashes)))
    frozen_calibration = replace(calibration, captures=tuple(
        replace(item, collection_check=replace(item.collection_check,
            artifact_hashes=MappingProxyType(dict(item.collection_check.artifact_hashes))))
        if item.collection_check else item for item in calibration.captures
    )) if calibration else None
    with _LOCK:
        if len(_RECEIPTS) >= 4096:
            _RECEIPTS.pop(next(iter(_RECEIPTS)))
        _RECEIPTS[validation_id] = _Receipt(binding, frozen_check, json.loads(json.dumps(result)), frozen_calibration)
    return result


def backend_validation(row: Mapping[str, Any]) -> dict[str, Any]:
    _, identity_reasons = canonical_identity(row)
    if identity_reasons:
        return {"schema_version": SCHEMA_VERSION, "status": "blocked", "reason_codes": identity_reasons}
    claim = row.get("attribution_validation")
    claim = claim if isinstance(claim, dict) else {}
    validation_id = claim.get("validation_id")
    with _LOCK:
        receipt = _RECEIPTS.get(validation_id) if isinstance(validation_id, str) else None
    if receipt is None:
        return {"schema_version": SCHEMA_VERSION, "status": "blocked",
                "reason_codes": ["backend_validation_unrecognized" if validation_id else "backend_validation_missing"]}
    try:
        if _binding(row) != receipt.binding:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        return {"schema_version": SCHEMA_VERSION, "status": "blocked", "reason_codes": ["validation_identity_mismatch"]}
    changed = _artifact_reasons(receipt.check)
    if receipt.result["status"] == "verified":
        changed.extend(receipt.calibration.evaluate()["reason_codes"] if receipt.calibration else ["calibration_missing"])
    if changed:
        return {**receipt.result, "status": "blocked", "reason_codes": list(dict.fromkeys([*receipt.result["reason_codes"], *changed]))}
    return json.loads(json.dumps(receipt.result))


def validate_live_mma_observations(profiler_result: Any, case_root: Path, report_path: Path,
                                   context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Attach backend findings to a live collection; imports never call this.

    Current native artifacts have no supported launch identity trace. Observed
    identities stay unknown rather than copying the requested identity/sidecar.
    A future native adapter must populate those facts from original artifacts.
    """
    expected = CollectionIdentity(
        collection_id=context.get("collection_id"), source_trial_id=context.get("source_trial_id"),
        source_sha256=context.get("source_sha256"), device_id=context.get("device_id"),
        shape_id=context.get("shape_id"), dtype=context.get("dtype"),
        kernel_name=context.get("requested_kernel"), tool_version=context.get("tool_version"),
        tool_build=context.get("tool_build"),
    )
    observed = CollectionIdentity(tool_version=context.get("tool_version"), tool_build=context.get("tool_build"))
    config_path = case_root / "mcProfiler.json"
    hashes = {report_path.relative_to(case_root).as_posix(): hashlib.sha256(report_path.read_bytes()).hexdigest()}
    if config_path.is_file() and not config_path.is_symlink():
        hashes["mcProfiler.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    check = CollectionCheck(case_root, expected, observed, hashes,
        report_path=report_path.relative_to(case_root).as_posix(),
        config_path="mcProfiler.json" if "mcProfiler.json" in hashes else None)
    validations = []
    for observation in profiler_result.observations:
        if observation.metric_name != "mma_duty":
            continue
        row = observation.to_dict()
        validation = validate_mma_collection(row, check)
        observation.attribution_validation = validation
        validations.append(validation)
    profiler_result.raw_artifact_refs["metric_attribution_validation"] = validations
    return validations
