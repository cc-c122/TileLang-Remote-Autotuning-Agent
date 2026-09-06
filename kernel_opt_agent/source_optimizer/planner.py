from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .analyzer import CopyLoopTarget


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    template: str = "parallel_copy_to_t_copy"
    hypothesis: str
    evidence_ids: list[str] = Field(default_factory=list)


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
    evidence_ids: list[str],
    client: Any | None = None,
) -> tuple[SourcePlan, str]:
    if client is not None:
        prompt = {
            "role": "user",
            "content": (
                "Select exactly one authorized target and the template parallel_copy_to_t_copy. "
                "Return strict JSON with target_id, template, hypothesis, evidence_ids. "
                f"Authorized target_ids: {[item.target_id for item in targets]}. "
                f"Authorized evidence_ids: {evidence_ids}. Do not return source code or shell commands."
            ),
        }
        for _ in range(2):
            try:
                return _validate_plan(client.chat_json([prompt]), targets, set(evidence_ids)), "llm"
            except Exception:
                continue
    target = targets[0]
    return (
        SourcePlan(
            target_id=target.target_id,
            hypothesis="replace a verified elementwise copy loop with the TileLang bulk copy primitive",
            evidence_ids=evidence_ids,
        ),
        "rule_based",
    )
