from __future__ import annotations

import math
from typing import Any

from .identity import canonical_identity


UNVERIFIED_ATTRIBUTION_WARNING = (
    "mcProfiler target attribution is unverified; counters may include other device activity. "
    "Excluded from optimization planning."
)


def planning_eligibility(row: dict[str, Any]) -> tuple[bool, list[str]]:
    _, reasons = canonical_identity(row)
    source = row.get("source")
    compiler_metrics = {"register_count", "shared_memory_bytes", "private_memory_bytes",
                        "local_memory_bytes", "spill_bytes", "spill_count"}
    if source in ("compiler_log", "tilelang_log"):
        if not isinstance(row.get("metric"), str) or row["metric"] not in compiler_metrics:
            reasons.append("source_metric_mismatch")
    elif source == "benchmark":
        if row.get("metric") != "latency_ms":
            reasons.append("source_metric_mismatch")
    elif source != "mcprofiler":
        reasons.append("source_unrecognized")
    value = row.get("value")
    try:
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        numeric = False
    if row.get("available") is not True or not numeric:
        reasons.append("metric_unavailable_or_non_numeric")
    if not isinstance(row.get("evidence_id"), str) or not row["evidence_id"].strip():
        reasons.append("evidence_id_missing")
    if not row.get("source") or not row.get("artifact_id"):
        reasons.append("provenance_missing")
    if row.get("parse_confidence") not in ("medium", "high"):
        reasons.append("parse_confidence_insufficient")
    if row.get("confidence") not in ("medium", "high"):
        reasons.append("confidence_insufficient")
    if row.get("attribution_status") != "verified" or row.get("attribution_confidence") not in ("medium", "high"):
        reasons.append("target_attribution_unverified")
    if row.get("scope") != "kernel" or not isinstance(row.get("kernel_name"), str) or not row["kernel_name"].strip():
        reasons.append("kernel_scope_unconfirmed")
    if row.get("source") == "mcprofiler":
        context = row.get("collection_scope")
        context = context if isinstance(context, dict) else {}
        requested = context.get("requested_kernel")
        if not requested:
            reasons.append("target_kernel_unconfirmed")
        elif requested != row.get("kernel_name"):
            reasons.append("target_kernel_mismatch")
        if context.get("counter_scope") != "kernel":
            reasons.append("counter_scope_unknown")
        if context.get("background_workload_excluded") is not True:
            reasons.append("background_workload_not_excluded")
        if not row.get("device_id"):
            reasons.append("device_binding_unknown")
        if not row.get("tool_version"):
            reasons.append("tool_version_unknown")
        definition = row.get("metric_definition")
        if not isinstance(definition, dict) or definition.get("status") != "documented":
            reasons.append("metric_definition_unconfirmed")
        from .mcprofiler.attribution_validation import backend_validation

        validation = backend_validation(row)
        if validation["status"] != "verified":
            reasons.append("metric_attribution_not_validated")
            reasons.extend(validation["reason_codes"])
    return not reasons, reasons


def annotate_planning_eligibility(row: dict[str, Any]) -> dict[str, Any]:
    # Recompute even when an imported row claims planning_eligible=true.
    row = dict(row)
    if row.get("source") == "mcprofiler":
        from .mcprofiler.attribution_validation import backend_validation

        validation = backend_validation(row)
        row["attribution_validation"] = validation
        raw_warnings = row.get("parse_warnings")
        warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
        if validation["status"] == "verified":
            warnings = [item for item in warnings if item != UNVERIFIED_ATTRIBUTION_WARNING]
            row.update(confidence=row.get("parse_confidence", "low"), attribution_status="verified",
                       attribution_confidence="high", scope="kernel", parse_warnings=warnings,
                       metric_definition={"status": "documented", **validation["definition"]})
        else:
            if UNVERIFIED_ATTRIBUTION_WARNING not in warnings:
                warnings.append(UNVERIFIED_ATTRIBUTION_WARNING)
            row.update(confidence="low", attribution_status="unverified", attribution_confidence="low", parse_warnings=warnings)
    eligible, reasons = planning_eligibility(row)
    return {**row, "planning_eligible": eligible, "eligibility_reasons": reasons}
