from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import EvidenceBundle, EvidenceRecord


BOTTLENECK_TYPES = [
    "compute_underutilization",
    "memory_bound",
    "excessive_hbm_write",
    "private_memory_spill",
    "shared_memory_pressure",
    "shared_bank_conflict",
    "poor_memory_coalescing",
    "low_occupancy",
    "pipeline_ineffective",
    "hardware_intrinsic_missing",
    "insufficient_evidence",
]

CONFIDENCE_LEVELS = {"low", "medium", "high"}


@dataclass
class BottleneckDiagnosis:
    bottleneck_type: str
    confidence: str
    evidence: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    source_trial_id: str | None = None
    source_metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"unsupported diagnosis confidence: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bottleneck_type": self.bottleneck_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "uncertainty": self.uncertainty,
            "recommended_actions": self.recommended_actions,
            "source_trial_id": self.source_trial_id,
            "source_metrics": self.source_metrics,
        }


@dataclass
class EvidenceDiagnosis:
    bottleneck_type: str
    confidence: str
    evidence_ids: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"unsupported diagnosis confidence: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bottleneck_type": self.bottleneck_type,
            "confidence": self.confidence,
            "evidence_ids": self.evidence_ids,
            "counter_evidence": self.counter_evidence,
            "uncertainty": self.uncertainty,
            "recommended_actions": self.recommended_actions,
        }


def _available_evidence(records: list[EvidenceRecord], metric: str) -> list[EvidenceRecord]:
    return [item for item in records if item.metric == metric and item.available]


def _first_number(records: list[EvidenceRecord], metric: str) -> tuple[EvidenceRecord, float] | None:
    for item in _available_evidence(records, metric):
        try:
            return item, float(item.value)
        except (TypeError, ValueError):
            continue
    return None


def _has_metric(records: list[EvidenceRecord], metric: str) -> bool:
    return bool(_available_evidence(records, metric))


def diagnose_from_evidence_records(records: list[EvidenceRecord]) -> list[EvidenceDiagnosis]:
    available = [item for item in records if item.available]
    diagnoses: list[EvidenceDiagnosis] = []

    if not available:
        return [
            EvidenceDiagnosis(
                bottleneck_type="insufficient_evidence",
                confidence="low",
                evidence_ids=[],
                uncertainty=["profiler did not provide normalized evidence records"],
                recommended_actions=["collect mcProfiler or backend profiler metrics before bottleneck diagnosis"],
            )
        ]
    supported_metrics = {
        "private_read_instructions",
        "private_write_instructions",
        "shared_memory_access_efficiency",
        "shared_conflict_cycles",
        "l2c_hit_rate",
        "vl1_hit_rate",
        "dnoc_read_average_latency",
        "mma_duty",
        "achieved_waves",
        "dispatched_waves",
        "private_memory_bytes",
        "local_memory_bytes",
        "spill_bytes",
        "spill_count",
    }
    unknown_ids = [item.evidence_id for item in available if item.metric not in supported_metrics]
    supported_ids = [item.evidence_id for item in available if item.metric in supported_metrics]
    if unknown_ids and not supported_ids:
        return [
            EvidenceDiagnosis(
                bottleneck_type="insufficient_evidence",
                confidence="low",
                evidence_ids=unknown_ids,
                uncertainty=["available profiler evidence does not map to a supported B-1 bottleneck rule"],
                recommended_actions=["extend metric mapping or collect supported mcProfiler fields"],
            )
        ]

    private_read = _first_number(records, "private_read_instructions")
    private_write = _first_number(records, "private_write_instructions")
    private_bytes = _first_number(records, "private_memory_bytes")
    local_bytes = _first_number(records, "local_memory_bytes")
    spill_bytes = _first_number(records, "spill_bytes")
    spill_count = _first_number(records, "spill_count")
    private_ids: list[str] = []
    if private_read and private_read[1] > 0:
        private_ids.append(private_read[0].evidence_id)
    if private_write and private_write[1] > 0:
        private_ids.append(private_write[0].evidence_id)
    compiler_private_ids = []
    for item in (private_bytes, local_bytes, spill_bytes, spill_count):
        if item and item[1] > 0:
            compiler_private_ids.append(item[0].evidence_id)
    if private_ids and compiler_private_ids:
        diagnoses.append(
            EvidenceDiagnosis(
                bottleneck_type="private_memory_spill",
                confidence="medium",
                evidence_ids=private_ids + compiler_private_ids,
                counter_evidence=[],
                uncertainty=["private memory traffic is paired with compiler/local/spill evidence"],
                recommended_actions=["inspect generated code for local/private memory usage", "reduce per-thread temporary storage"],
            )
        )

    if unknown_ids and not diagnoses:
        diagnoses.append(
            EvidenceDiagnosis(
                bottleneck_type="insufficient_evidence",
                confidence="low",
                evidence_ids=unknown_ids,
                uncertainty=["available profiler evidence does not map to a supported B-1 bottleneck rule"],
                recommended_actions=["extend metric mapping or collect supported mcProfiler fields"],
            )
        )
    if not diagnoses:
        sample_ids = [item.evidence_id for item in available[:3]]
        diagnoses.append(
            EvidenceDiagnosis(
                bottleneck_type="insufficient_evidence",
                confidence="low",
                evidence_ids=sample_ids,
                uncertainty=[
                    "available evidence is not enough for a supported B-1 bottleneck conclusion",
                    "MetaX-specific thresholds are not established for shared memory, cache hit rates, DNoC latency, MMA duty, or wave counts",
                ],
                recommended_actions=["collect paired baseline/variant profiler cases or backend thresholds before bottleneck diagnosis"],
            )
        )
    return diagnoses


def _missing(bundle: EvidenceBundle, *names: str) -> list[str]:
    return [f"{name} unavailable" for name in names if not bundle.has_metric(name)]


def _hardware_number(bundle: EvidenceBundle, name: str) -> float | None:
    value = bundle.hardware_value(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record(
    bundle: EvidenceBundle,
    kind: str,
    confidence: str,
    evidence: list[str],
    uncertainty: list[str],
    actions: list[str],
) -> BottleneckDiagnosis:
    if not evidence:
        confidence = "low"
        uncertainty = uncertainty or ["insufficient profiler evidence"]
    return BottleneckDiagnosis(
        kind,
        confidence,
        evidence,
        uncertainty,
        actions,
        source_trial_id=bundle.source_trial_id,
        source_metrics=bundle.metrics_snapshot(),
    )


def diagnose_bottlenecks(bundle: EvidenceBundle) -> list[BottleneckDiagnosis]:
    diagnoses: list[BottleneckDiagnosis] = []

    occupancy = bundle.metric("occupancy")
    warp_active_ratio = bundle.metric("warp_active_ratio")
    tflops = bundle.metric("tflops")
    compute_evidence: list[str] = []
    if occupancy is not None and occupancy < 0.5:
        compute_evidence.append(f"occupancy={occupancy} below 0.5")
    if warp_active_ratio is not None and warp_active_ratio < 0.6:
        compute_evidence.append(f"warp_active_ratio={warp_active_ratio} below 0.6")
    diagnoses.append(
        _record(
            bundle,
            "compute_underutilization",
            "medium" if len(compute_evidence) > 1 else "low",
            compute_evidence,
            _missing(bundle, "occupancy", "warp_active_ratio") + (["no hardware peak TFLOPS available"] if tflops is not None else ["tflops unavailable"]),
            ["improve_thread_mapping", "hardware_intrinsic_missing"],
        )
    )

    estimated_bw = bundle.metric("estimated_hbm_bandwidth")
    read_bw = bundle.metric("hbm_read_bandwidth")
    write_bw = bundle.metric("hbm_write_bandwidth")
    memory_evidence = []
    peak_bw = _hardware_number(bundle, "hbm_bandwidth_gbps") or _hardware_number(bundle, "memory_bandwidth_gbps")
    observed_bw = max([v for v in [estimated_bw, read_bw, write_bw] if v is not None], default=None)
    if observed_bw is not None:
        memory_evidence.append(f"observed memory bandwidth={observed_bw}")
        if peak_bw is not None and observed_bw >= peak_bw * 0.7:
            memory_evidence.append(f"observed bandwidth is >=70% of hardware bandwidth {peak_bw}")
    diagnoses.append(
        _record(
            bundle,
            "memory_bound",
            "medium" if peak_bw is not None and len(memory_evidence) > 1 else "low",
            memory_evidence,
            _missing(bundle, "estimated_hbm_bandwidth", "hbm_read_bandwidth", "hbm_write_bandwidth")
            + ([] if peak_bw is not None else ["hardware peak memory bandwidth unavailable"]),
            ["vectorized_load", "double_buffer", "improve_thread_mapping"],
        )
    )

    write_evidence = []
    if write_bw is not None:
        write_evidence.append(f"hbm_write_bandwidth={write_bw}")
        if read_bw is not None and write_bw > read_bw:
            write_evidence.append(f"hbm_write_bandwidth exceeds hbm_read_bandwidth={read_bw}")
    diagnoses.append(
        _record(
            bundle,
            "excessive_hbm_write",
            "medium" if len(write_evidence) > 1 else "low",
            write_evidence,
            _missing(bundle, "hbm_write_bandwidth") + ([] if read_bw is not None else ["hbm_read_bandwidth unavailable"]),
            ["reduce_hbm_store"],
        )
    )

    private_bytes = bundle.metric("private_memory_bytes")
    private_evidence = [f"private_memory_bytes={private_bytes}"] if private_bytes and private_bytes > 0 else []
    diagnoses.append(
        _record(
            bundle,
            "private_memory_spill",
            "medium",
            private_evidence,
            _missing(bundle, "private_memory_bytes"),
            ["reduce_private_memory"],
        )
    )

    shared_bytes = bundle.metric("shared_memory_bytes")
    shared_limit = _hardware_number(bundle, "shared_memory_per_block_bytes")
    shared_evidence = []
    if shared_bytes is not None:
        shared_evidence.append(f"shared_memory_bytes={shared_bytes}")
        if shared_limit is not None and shared_bytes >= shared_limit * 0.75:
            shared_evidence.append(f"shared memory uses >=75% of per-block limit {shared_limit}")
    diagnoses.append(
        _record(
            bundle,
            "shared_memory_pressure",
            "medium" if len(shared_evidence) > 1 else "low",
            shared_evidence,
            _missing(bundle, "shared_memory_bytes") + ([] if shared_limit is not None else ["shared memory per block limit unavailable"]),
            ["shared_layout_swizzle", "reduce_private_memory"],
        )
    )

    bank_conflict = bundle.metric("shared_bank_conflict")
    bank_evidence = [f"shared_bank_conflict={bank_conflict}"] if bank_conflict and bank_conflict > 0 else []
    diagnoses.append(
        _record(
            bundle,
            "shared_bank_conflict",
            "medium",
            bank_evidence,
            _missing(bundle, "shared_bank_conflict"),
            ["shared_layout_swizzle"],
        )
    )

    coalescing = bundle.metric("memory_coalescing_efficiency")
    coalescing_evidence = [f"memory_coalescing_efficiency={coalescing} below 0.8"] if coalescing is not None and coalescing < 0.8 else []
    diagnoses.append(
        _record(
            bundle,
            "poor_memory_coalescing",
            "medium",
            coalescing_evidence,
            _missing(bundle, "memory_coalescing_efficiency"),
            ["vectorized_load", "improve_thread_mapping"],
        )
    )

    low_occ_evidence = [f"occupancy={occupancy} below 0.5"] if occupancy is not None and occupancy < 0.5 else []
    diagnoses.append(
        _record(
            bundle,
            "low_occupancy",
            "medium",
            low_occ_evidence,
            _missing(bundle, "occupancy"),
            ["improve_thread_mapping", "reduce_private_memory"],
        )
    )

    pipeline_evidence = []
    if warp_active_ratio is not None and warp_active_ratio < 0.6:
        pipeline_evidence.append(f"warp_active_ratio={warp_active_ratio} suggests latency is not hidden")
    if estimated_bw is not None and occupancy is not None and occupancy >= 0.5 and estimated_bw < 1.0:
        pipeline_evidence.append("memory bandwidth is low despite non-low occupancy")
    diagnoses.append(
        _record(
            bundle,
            "pipeline_ineffective",
            "low",
            pipeline_evidence,
            _missing(bundle, "warp_active_ratio", "estimated_hbm_bandwidth"),
            ["double_buffer"],
        )
    )

    raw_text = bundle.raw_text().lower()
    intrinsic_evidence = []
    if "no tensor" in raw_text or "no mma" in raw_text or "intrinsic missing" in raw_text:
        intrinsic_evidence.append("logs mention missing tensor/mma intrinsic")
    diagnoses.append(
        _record(
            bundle,
            "hardware_intrinsic_missing",
            "low",
            intrinsic_evidence,
            ["generated code intrinsic usage is not confirmed"],
            ["hardware_intrinsic_missing"],
        )
    )

    return diagnoses
