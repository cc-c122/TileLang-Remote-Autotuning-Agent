from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from kernel_opt_agent.diagnosis.evidence import planning_evidence_allowed
from kernel_opt_agent.redaction import redact_data

from .analyzer import SourceOptimizationTarget


class PlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["attempt", "stop", "insufficient_evidence"]
    bottleneck_type: str = Field(min_length=1, max_length=128)
    confidence: Literal["low", "medium", "high"]
    evidence_ids: list[str]
    target_id: str | None
    template: str | None
    hypothesis: str = Field(min_length=1, max_length=2000)
    expected_effect: str = Field(min_length=1, max_length=1000)
    risk: str = Field(min_length=1, max_length=1000)
    validation_plan: list[str] = Field(min_length=1, max_length=12)


class SourcePlan(PlanProposal):
    planning_attempts: int = 0
    fallback_reason: str | None = None
    unsupported_reason: str | None = None


def _validate_plan(
    raw: dict[str, Any],
    targets: tuple[SourceOptimizationTarget, ...],
    allowed_evidence_ids: set[str],
) -> SourcePlan:
    plan = SourcePlan(**PlanProposal.model_validate(raw).model_dump())
    if len(set(plan.evidence_ids)) != len(plan.evidence_ids) or any(item not in allowed_evidence_ids for item in plan.evidence_ids):
        raise ValueError("LLM cited an unknown, ineligible, or duplicate evidence id")
    if plan.bottleneck_type not in {"source_structure", "insufficient_evidence"} and not plan.evidence_ids:
        raise ValueError("a hardware bottleneck claim requires eligible metric evidence")
    if not plan.evidence_ids and plan.confidence != "low":
        raise ValueError("source/log-only analysis must have low confidence")
    if any(not item.strip() for item in plan.validation_plan):
        raise ValueError("validation_plan must contain non-empty verification steps")
    if plan.decision != "attempt":
        if plan.target_id is not None or plan.template is not None:
            raise ValueError("stop/insufficient_evidence must not authorize a modification")
        return plan
    target = next((item for item in targets if item.target_id == plan.target_id), None)
    if target is None or not plan.template:
        raise ValueError("LLM selected an unauthorized or stale source target")
    if plan.template not in {item.template for item in targets}:
        plan.decision = "stop"
        plan.unsupported_reason = "unsupported_transformation: the proposed template is not implemented for current authorized targets"
        plan.target_id = None
        return plan
    if plan.template != target.template:
        raise ValueError("LLM selected a template that is not authorized for the source target")
    return plan


def choose_plan(
    targets: tuple[SourceOptimizationTarget, ...],
    evidence: list[Any],
    client: Any | None = None,
    *,
    context: dict[str, Any] | None = None,
    redactor: Callable[[Any], Any] | None = None,
) -> tuple[SourcePlan, str]:
    sanitize = redactor or (lambda value: redact_data(None, value))
    evidence_rows = [item for item in evidence if isinstance(item, dict) and planning_evidence_allowed(item)]
    evidence_ids = [str(item["evidence_id"]) for item in evidence_rows if item.get("evidence_id")]
    target_summaries = [
        {
            "target_id": item.target_id,
            "function": item.function_name,
            "source": item.source_expr,
            "destination": item.destination_expr,
            "extent": item.extent_expr,
            "template": item.template,
            "template_description": item.hypothesis,
            "confidence": item.confidence,
        }
        for item in targets
    ]
    last_error: str | None = None
    if client is not None:
        payload = sanitize({"authorized_targets": target_summaries, "eligible_metrics": evidence_rows,
                            "authorized_evidence_ids": evidence_ids, "feedback": context or {}})
        prompt = {
            "role": "user",
            "content": (
                "Plan one controlled source modification, or stop, or report insufficient_evidence. "
                "Return only strict JSON matching this schema: " + json.dumps(PlanProposal.model_json_schema(), sort_keys=True) + ". "
                "Provide concise analysis conclusions only; do not provide chain of thought or internal reasoning. "
                "Use only currently authorized target IDs/templates and eligible evidence IDs. "
                "No source code, shell commands, test modifications, new parameter search, or claimed performance gains. "
                "Only target-attribution-verified metrics are eligible. Parsing confidence does not establish target attribution. Missing metrics remain null. "
                "Without eligible metrics use bottleneck_type=source_structure or insufficient_evidence, confidence=low; "
                "a source/log hypothesis is not a measured hardware bottleneck. Explain expected_effect, risk and validation_plan. "
                "Source/log-only structural experiments are permitted, not required: an authorized target may be tried "
                "with bottleneck_type=source_structure, confidence=low, evidence_ids=[], and an explicitly unmeasured expected effect. "
                "Use the current authorized source excerpts and template description to justify that hypothesis; "
                "the unchanged build, correctness and independent performance gates decide acceptance. "
                "Stop remains permitted when the supplied source/log context cannot justify an authorized experiment. "
                "If the needed change is unsupported, say so; do not select an unrelated change. "
                + "\nPLANNING_CONTEXT_JSON:\n" + json.dumps(payload, sort_keys=True, default=str)
            ),
        }
        for attempt in range(1, 3):
            try:
                plan = _validate_plan(client.chat_json([prompt]), targets, set(evidence_ids))
                plan = SourcePlan.model_validate(sanitize(plan.model_dump()))
                plan.planning_attempts = attempt
                return plan, "llm"
            except Exception as exc:
                # Rejected responses can contain secrets or internal reasoning; never persist them.
                last_error = str(sanitize(f"{type(exc).__name__}: invalid planner response"))
                prompt = {**prompt, "content": prompt["content"] + "\nPrevious response rejected. Return complete valid JSON within the stated authorization."}
    common = dict(
        bottleneck_type="source_structure" if targets else "insufficient_evidence", confidence="low",
        evidence_ids=[], planning_attempts=2 if client is not None else 0,
        fallback_reason=last_error or "LLM planner was not configured",
        expected_effect="unmeasured; validate with independent repeated benchmark",
        risk="low-confidence structural hypothesis; compilation or correctness may fail",
        validation_plan=["syntax/build", "trusted-reference correctness", "independent repeated benchmark", "unchanged performance gate and parent recheck"],
    )
    if not targets:
        return SourcePlan(decision="stop", target_id=None, template=None,
                          hypothesis="no unattempted authorized source transformation remains", **common), "rule_based"
    target = targets[0]
    return SourcePlan(decision="attempt", target_id=target.target_id, template=target.template,
                      hypothesis=target.hypothesis, **common), "rule_based"
