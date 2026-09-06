from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricMapping:
    metric_name: str
    section: str
    source_field_name: str
    unit: str | None
    confidence: str = "high"
    warnings: tuple[str, ...] = ()


PERCENT_WARNING = (
    "percentage-like mcProfiler field retained as reported; statistical denominator is not normalized or capped",
)


METRIC_MAPPINGS: tuple[MetricMapping, ...] = (
    MetricMapping("global_read_instructions", "Memory Statistics", "Global Read Instructions", "instructions"),
    MetricMapping("global_write_instructions", "Memory Statistics", "Global Write Instructions", "instructions"),
    MetricMapping("private_read_instructions", "Memory Statistics", "Private Read Instructions", "instructions"),
    MetricMapping("private_write_instructions", "Memory Statistics", "Private Write Instructions", "instructions"),
    MetricMapping("vl1_hit_rate", "Memory Statistics", "VL1 Hit Rate", "percent", warnings=PERCENT_WARNING),
    MetricMapping("l2c_hit_rate", "Memory Statistics", "L2C Hit Rate", "percent", warnings=PERCENT_WARNING),
    MetricMapping("dnoc_read_average_latency", "Memory Statistics", "Dnoc Read Average Latency", "cycles"),
    MetricMapping(
        "shared_memory_access_efficiency",
        "Workgroup Memory",
        "shared memory access efficiency",
        "percent",
        warnings=PERCENT_WARNING,
    ),
    MetricMapping(
        "shared_conflict_cycles",
        "Workgroup Memory",
        "average conflict cycles per instruction",
        "cycles_per_instruction",
    ),
    MetricMapping("achieved_waves", "Occupancy", "Achieved waves", "waves"),
    MetricMapping("dispatched_waves", "Occupancy", "Dispatched waves", "waves"),
    MetricMapping("mma_duty", "GPU Throughput Statistics", "AP MMA Duty ratio", "percent", warnings=PERCENT_WARNING),
)


MAPPING_BY_SOURCE = {(item.section, item.source_field_name): item for item in METRIC_MAPPINGS}
