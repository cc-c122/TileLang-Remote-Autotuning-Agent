from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Source = Literal["user_config", "remote_detection", "builtin_profile", "doc_lookup", "safe_probe", "unknown"]
Confidence = Literal["high", "medium", "low", "unknown"]


CANONICAL_FIELDS = [
    "target_name",
    "backend",
    "os",
    "platform",
    "python_version",
    "device_count",
    "driver_version",
    "runtime_version",
    "compiler_version",
    "tilelang_version",
    "mctilelang_version",
    "total_memory_GB",
    "available_memory_GB",
    "warp_size",
    "wave_size",
    "max_threads_per_block",
    "shared_memory_per_block_bytes",
    "max_registers_per_thread",
    "vector_alignment_bytes",
    "supported_dtypes",
]


@dataclass
class HardwareField:
    value: Any = None
    source: Source = "unknown"
    confidence: Confidence = "unknown"
    notes: str = "not detected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class HardwareInfo:
    fields: dict[str, HardwareField] = field(default_factory=dict)
    profile_used: str | None = None
    detection_enabled: bool = True
    remote_detection_attempted: bool = False
    doc_lookup_used: bool = False
    safe_probe_used: bool = False
    safe_probe_results: list[dict[str, Any]] = field(default_factory=list)
    conservative_mode: bool = True
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def unknown(cls) -> "HardwareInfo":
        return cls(fields={name: HardwareField() for name in CANONICAL_FIELDS})

    def set_field(self, name: str, value: Any, source: Source, confidence: Confidence, notes: str) -> None:
        self.fields[name] = HardwareField(value=value, source=source, confidence=confidence, notes=notes)

    def unknown_fields(self) -> list[str]:
        return [
            name
            for name, field_value in self.fields.items()
            if field_value.source == "unknown" or field_value.value is None or field_value.value == "unknown"
        ]

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for field_value in self.fields.values():
            counts[field_value.source] = counts.get(field_value.source, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_used": self.profile_used,
            "detection_enabled": self.detection_enabled,
            "remote_detection_attempted": self.remote_detection_attempted,
            "doc_lookup_used": self.doc_lookup_used,
            "safe_probe_used": self.safe_probe_used,
            "safe_probe_results": self.safe_probe_results,
            "conservative_mode": self.conservative_mode,
            "unknown_fields": self.unknown_fields(),
            "source_counts": self.source_counts(),
            "warnings": self.warnings,
            "fields": {name: field_value.to_dict() for name, field_value in sorted(self.fields.items())},
        }
