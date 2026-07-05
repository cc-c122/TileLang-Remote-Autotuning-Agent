from __future__ import annotations

from typing import Any

from kernel_opt_agent.profiler.base import METRIC_FIELDS


def profiler_metrics_table(profiler: dict[str, Any] | None) -> list[str]:
    result = ((profiler or {}).get("result") or {}) if profiler else {}
    lines = [
        "",
        "## Profiler Metrics",
        "",
        "| Metric | Value | Available |",
        "| --- | --- | --- |",
    ]
    available = result.get("available_metrics") or {}
    for name in METRIC_FIELDS:
        value = result.get(name)
        rendered = "null" if value is None else str(value)
        lines.append(f"| {name} | {rendered} | {bool(available.get(name))} |")
    error = (profiler or {}).get("error") if profiler else None
    if error:
        lines.append("")
        lines.append(f"- Profiler error: {error}")
    return lines


def diagnosis_records(diagnoses: list[dict[str, Any]] | None) -> list[str]:
    lines = [
        "",
        "## Bottleneck Diagnosis",
        "",
        "| Source Trial | Bottleneck | Confidence | Evidence | Uncertainty | Recommended Actions |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in diagnoses or []:
        lines.append(
            "| {source} | {kind} | {confidence} | {evidence} | {uncertainty} | {actions} |".format(
                source=item.get("source_trial_id") or "unknown",
                kind=item.get("bottleneck_type"),
                confidence=item.get("confidence"),
                evidence="<br>".join(item.get("evidence") or ["none"]),
                uncertainty="<br>".join(item.get("uncertainty") or ["none"]),
                actions=", ".join(item.get("recommended_actions") or []),
            )
        )
    if not diagnoses:
        lines.append("| unknown | none | low | none | no diagnosis records available | none |")
    return lines
