from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any

from kernel_opt_agent.profiler.base import (
    METRIC_FIELDS, MetricObservation, ProfilerResult, UNVERIFIED_ATTRIBUTION_WARNING,
)
from kernel_opt_agent.profiler.attribution import annotate_planning_eligibility, planning_eligibility


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
    source_trial_id: str | None = None
    parse_confidence: str | None = None
    attribution_status: str = "unverified"
    attribution_confidence: str = "unknown"
    scope: str = "unknown"
    kernel_name: str | None = None
    device_id: str | None = None
    tool_version: str | None = None
    metric_definition: dict[str, Any] = field(default_factory=dict)
    collection_scope: dict[str, Any] = field(default_factory=dict)
    attribution_validation: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.parse_confidence is None:
            self.parse_confidence = self.confidence
        if self.source == "mcprofiler":
            payload = annotate_planning_eligibility(asdict(self))
            for name in ("confidence", "attribution_status", "attribution_confidence", "parse_warnings",
                         "attribution_validation", "scope", "metric_definition"):
                setattr(self, name, payload[name])

    def to_dict(self) -> dict[str, Any]:
        return annotate_planning_eligibility(redact_sensitive(
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
                "source_trial_id": self.source_trial_id,
                "parse_confidence": self.parse_confidence,
                "attribution_status": self.attribution_status,
                "attribution_confidence": self.attribution_confidence,
                "scope": self.scope,
                "kernel_name": self.kernel_name,
                "device_id": self.device_id,
                "tool_version": self.tool_version,
                "metric_definition": self.metric_definition,
                "collection_scope": self.collection_scope,
                "attribution_validation": self.attribution_validation,
            }
        ))


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
        source_trial_id=trial_id,
        parse_confidence=observation.parse_confidence,
        attribution_status=observation.attribution_status,
        attribution_confidence=observation.attribution_confidence,
        scope=observation.scope,
        kernel_name=observation.kernel_name,
        device_id=observation.device_id,
        tool_version=observation.tool_version,
        metric_definition=dict(observation.metric_definition),
        collection_scope=dict(observation.collection_scope),
        attribution_validation=dict(observation.attribution_validation),
    )


def planning_evidence_allowed(row: dict[str, Any]) -> bool:
    return planning_eligibility(row)[0]


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
