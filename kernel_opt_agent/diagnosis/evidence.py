from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kernel_opt_agent.profiler.base import METRIC_FIELDS, ProfilerResult


@dataclass
class EvidenceBundle:
    profiler: ProfilerResult
    hardware_fields: dict[str, Any] = field(default_factory=dict)
    source_trial_id: str | None = None
    source_metrics: dict[str, Any] = field(default_factory=dict)

    def metric(self, name: str) -> Any:
        return getattr(self.profiler, name, None)

    def has_metric(self, name: str) -> bool:
        return bool(self.profiler.available_metrics.get(name))

    def hardware_value(self, name: str) -> Any:
        field = self.hardware_fields.get(name)
        if isinstance(field, dict):
            return field.get("value")
        return field

    def raw_text(self) -> str:
        return "\n".join(str(value) for value in self.profiler.raw_logs.values() if value)

    def metrics_snapshot(self) -> dict[str, Any]:
        snapshot = {name: getattr(self.profiler, name, None) for name in METRIC_FIELDS}
        snapshot.update(self.source_metrics)
        return snapshot
