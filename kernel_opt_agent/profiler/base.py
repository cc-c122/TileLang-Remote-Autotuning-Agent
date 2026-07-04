from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


METRIC_FIELDS = [
    "latency",
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
]


@dataclass
class ProfilerResult:
    latency: float | None = None
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
    raw_logs: dict[str, str] = field(default_factory=dict)
    available_metrics: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        availability = dict(self.available_metrics)
        for name in METRIC_FIELDS:
            availability[name] = getattr(self, name) is not None if name not in availability else bool(availability[name])
        self.available_metrics = availability

    @classmethod
    def empty(cls, raw_logs: dict[str, str] | None = None) -> "ProfilerResult":
        return cls(raw_logs=raw_logs or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "latency": self.latency,
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
            "raw_logs": self.raw_logs,
            "available_metrics": self.available_metrics,
        }


class BaseProfiler:
    def collect(self, run_context: dict[str, Any]) -> ProfilerResult:
        raise NotImplementedError
