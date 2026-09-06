from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


METRIC_FIELDS = [
    "latency_ms",
    "tflops",
    "estimated_hbm_bandwidth",
    "register_count",
    "shared_memory_bytes",
    "private_memory_bytes",
    "occupancy",
    "warp_active_ratio",
    "hbm_read_bandwidth",
    "hbm_write_bandwidth",
    "shared_bank_conflict",
    "memory_coalescing_efficiency",
    "vl1_hit_rate",
    "l2c_hit_rate",
    "dnoc_read_average_latency",
    "shared_memory_access_efficiency",
    "shared_conflict_cycles",
    "achieved_waves",
    "dispatched_waves",
    "mma_duty",
]


@dataclass
class MetricObservation:
    metric_name: str
    source_field_name: str | None
    value: Any
    unit: str | None
    source: str
    available: bool
    confidence: str
    artifact: str | None
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(
            {
                "metric_name": self.metric_name,
                "source_field_name": self.source_field_name,
                "value": self.value,
                "unit": self.unit,
                "source": self.source,
                "available": self.available,
                "confidence": self.confidence,
                "artifact": self.artifact,
                "parse_warnings": self.parse_warnings,
            }
        )


@dataclass
class ProfilerResult:
    latency_ms: float | None = None
    tflops: float | None = None
    estimated_hbm_bandwidth: float | None = None
    register_count: int | None = None
    shared_memory_bytes: int | None = None
    private_memory_bytes: int | None = None
    occupancy: float | None = None
    warp_active_ratio: float | None = None
    hbm_read_bandwidth: float | None = None
    hbm_write_bandwidth: float | None = None
    shared_bank_conflict: float | None = None
    memory_coalescing_efficiency: float | None = None
    vl1_hit_rate: float | None = None
    l2c_hit_rate: float | None = None
    dnoc_read_average_latency: float | None = None
    shared_memory_access_efficiency: float | None = None
    shared_conflict_cycles: float | None = None
    achieved_waves: float | None = None
    dispatched_waves: float | None = None
    mma_duty: float | None = None
    raw_logs: dict[str, str] = field(default_factory=dict)
    available_metrics: dict[str, bool] = field(default_factory=dict)
    observations: list[MetricObservation] = field(default_factory=list)
    raw_artifact_refs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        availability = dict(self.available_metrics)
        for name in METRIC_FIELDS:
            availability[name] = getattr(self, name) is not None if name not in availability else bool(availability[name])
        self.available_metrics = availability

    @classmethod
    def empty(cls, raw_logs: dict[str, str] | None = None) -> "ProfilerResult":
        return cls(raw_logs=raw_logs or {})

    @property
    def latency(self) -> float | None:
        return self.latency_ms

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(
            {
                "latency_ms": self.latency_ms,
                "tflops": self.tflops,
                "estimated_hbm_bandwidth": self.estimated_hbm_bandwidth,
                "register_count": self.register_count,
                "shared_memory_bytes": self.shared_memory_bytes,
                "private_memory_bytes": self.private_memory_bytes,
                "occupancy": self.occupancy,
                "warp_active_ratio": self.warp_active_ratio,
                "hbm_read_bandwidth": self.hbm_read_bandwidth,
                "hbm_write_bandwidth": self.hbm_write_bandwidth,
                "shared_bank_conflict": self.shared_bank_conflict,
                "memory_coalescing_efficiency": self.memory_coalescing_efficiency,
                "vl1_hit_rate": self.vl1_hit_rate,
                "l2c_hit_rate": self.l2c_hit_rate,
                "dnoc_read_average_latency": self.dnoc_read_average_latency,
                "shared_memory_access_efficiency": self.shared_memory_access_efficiency,
                "shared_conflict_cycles": self.shared_conflict_cycles,
                "achieved_waves": self.achieved_waves,
                "dispatched_waves": self.dispatched_waves,
                "mma_duty": self.mma_duty,
                "raw_logs": self.raw_logs,
                "available_metrics": self.available_metrics,
                "observations": [item.to_dict() for item in self.observations],
                "raw_artifact_refs": self.raw_artifact_refs,
            }
        )


class BaseProfiler:
    def collect(self, run_context: dict[str, Any]) -> ProfilerResult:
        raise NotImplementedError
