from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from kernel_opt_agent.profiler.base import METRIC_FIELDS, MetricObservation, ProfilerResult


SENSITIVE_PATTERNS = (
    "password",
    "api_key",
    "token",
    "secret",
    "credential",
    "authorization",
    "bearer",
    "private_key",
    "private key",
)
REDACTED_SECRET = "<redacted:secret>"


def _is_sensitive_string(value: str) -> bool:
    lowered = value.lower()
    return any(pattern in lowered for pattern in SENSITIVE_PATTERNS)


def redact_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            str(key): REDACTED_SECRET if _is_sensitive_string(str(key)) else redact_sensitive(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_sensitive(item) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_sensitive(item) for item in data)
    if isinstance(data, str) and _is_sensitive_string(data):
        return REDACTED_SECRET
    return data


@dataclass
class EvidenceRecord:
    evidence_id: str
    metric: str
    value: Any
    unit: str | None
    source: str
    source_field: str | None
    artifact_id: str | None
    available: bool
    confidence: str
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(
            {
                "evidence_id": self.evidence_id,
                "metric": self.metric,
                "value": self.value,
                "unit": self.unit,
                "source": self.source,
                "source_field": self.source_field,
                "artifact_id": self.artifact_id,
                "available": self.available,
                "confidence": self.confidence,
                "parse_warnings": self.parse_warnings,
            }
        )


def make_evidence_id(trial_id: str | None, observation: MetricObservation, index: int) -> str:
    payload = {
        "trial_id": trial_id or "",
        "index": index,
        "metric": observation.metric_name,
        "source": observation.source,
        "source_field": observation.source_field_name,
        "artifact": observation.artifact,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"ev_{digest}"


def observation_to_evidence(
    observation: MetricObservation,
    trial_id: str | None = None,
    index: int = 0,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=make_evidence_id(trial_id, observation, index),
        metric=observation.metric_name,
        value=observation.value,
        unit=observation.unit,
        source=observation.source,
        source_field=observation.source_field_name,
        artifact_id=observation.artifact,
        available=observation.available,
        confidence=observation.confidence,
        parse_warnings=list(observation.parse_warnings),
    )


def profiler_observations_to_evidence(
    profiler: ProfilerResult,
    trial_id: str | None = None,
) -> list[EvidenceRecord]:
    return [observation_to_evidence(item, trial_id, index) for index, item in enumerate(profiler.observations)]


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
