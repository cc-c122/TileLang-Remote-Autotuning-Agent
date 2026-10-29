"""Append-only public audit contract for controlled source optimization rounds."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kernel_opt_agent.profiler.attribution import annotate_planning_eligibility
from kernel_opt_agent.diagnosis.evidence import planning_evidence_allowed


class OptimizationRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2.optimization_round.v1"] = "v2.optimization_round.v1"
    round_index: int = Field(ge=0)
    trial_id: str
    parent_source_sha256: str
    candidate_source_sha256: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    template_description: str | None = None
    diff: str = ""
    validation: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)


def append_round(results_dir: Path, record: OptimizationRound, redactor) -> None:
    def json_safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_safe(item) for item in value]
        return value

    payload = json_safe(redactor(record.model_dump(mode="json")))
    with (results_dir / "optimization_rounds.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            continue
    return rows


def bound_evidence(results_dir: Path, trial_id: str, source_sha256: str,
                   binding: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible, excluded = [], []
    for row in read_rows(results_dir / "metric_observations.jsonl"):
        annotated = annotate_planning_eligibility(row)
        scope = row.get("collection_scope") or {}
        digest = row.get("source_sha256") or (scope.get("source_sha256") if isinstance(scope, dict) else None)
        reasons = list(annotated["eligibility_reasons"])
        if row.get("source_trial_id") != trial_id or digest != source_sha256:
            reasons.append("current_parent_source_or_trial_mismatch")
        for field in ("device_id", "shape_id", "dtype"):
            expected = (binding or {}).get(field)
            actual = row.get(field) or (scope.get(field) if isinstance(scope, dict) else None)
            if expected is not None and actual != expected:
                reasons.append(field + "_binding_mismatch")
        kernels = (binding or {}).get("kernel_names") or []
        if kernels and row.get("kernel_name") not in kernels:
            reasons.append("target_kernel_binding_mismatch")
        if not reasons and planning_evidence_allowed(annotated):
            eligible.append(annotated)
        elif row.get("available") is True:
            excluded.append({"evidence_id": row.get("evidence_id"), "reasons": reasons})
    return eligible, excluded
