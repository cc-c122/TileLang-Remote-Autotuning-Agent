from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analyzer import CopyLoopTarget


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    template: str = "parallel_copy_to_t_copy"
    hypothesis: str
    evidence_ids: list[str] = Field(default_factory=list)
    planning_attempts: int = 0
    fallback_reason: str | None = None


def _validate_plan(raw: dict[str, Any], targets: tuple[CopyLoopTarget, ...], allowed_evidence_ids: set[str]) -> SourcePlan:
    plan = SourcePlan.model_validate(raw)
    if plan.template != "parallel_copy_to_t_copy":
        raise ValueError("LLM selected an unauthorized source optimization template")
    if plan.target_id not in {item.target_id for item in targets}:
        raise ValueError("LLM selected an unauthorized source target")
    if any(item not in allowed_evidence_ids for item in plan.evidence_ids):
        raise ValueError("LLM cited an unknown evidence id")
    return plan


def choose_plan(
    targets: tuple[CopyLoopTarget, ...],
    evidence: list[Any],
    client: Any | None = None,
) -> tuple[SourcePlan, str]:
    evidence_rows = [item for item in evidence if isinstance(item, dict)]
    evidence_ids = [str(item["evidence_id"]) for item in evidence_rows if item.get("evidence_id")]
    if not evidence_rows:
        evidence_ids = [str(item) for item in evidence if isinstance(item, str)]
    target_summaries = [
        {
            "target_id": item.target_id,
            "function": item.function_name,
            "source": item.source_expr,
            "destination": item.destination_expr,
            "extent": item.extent_expr,
        }
        for item in targets
    ]
    last_error: str | None = None
    if client is not None:
        prompt = {
            "role": "user",
            "content": (
                "Select exactly one authorized target and the template parallel_copy_to_t_copy. "
                "Return strict JSON with target_id, template, hypothesis, evidence_ids. "
                f"Authorized targets: {json.dumps(target_summaries, sort_keys=True)}. "
                f"Available evidence: {json.dumps(evidence_rows, sort_keys=True, default=str)}. "
                f"Authorized evidence_ids: {evidence_ids}. Do not return source code or shell commands."
            ),
        }
        for attempt in range(1, 3):
            try:
                plan = _validate_plan(client.chat_json([prompt]), targets, set(evidence_ids))
                plan.planning_attempts = attempt
                return plan, "llm"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
    target = targets[0]
    return (
        SourcePlan(
            target_id=target.target_id,
            hypothesis="replace a verified elementwise copy loop with the TileLang bulk copy primitive",
            evidence_ids=evidence_ids,
            planning_attempts=2 if client is not None else 0,
            fallback_reason=last_error or "LLM planner was not configured",
        ),
        "rule_based",
    )
