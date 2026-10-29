"""Collection contracts shared by native profiler adapters."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .base import MetricObservation, ProfilerResult, redact_sensitive


@dataclass(repr=False)
class ProfileContext:
    runner: Any
    results_dir: Path
    request: Any
    should_cancel: Callable[[], bool] | None = None
    redactor: Callable[[Any], Any] | None = None
    secret_values: tuple[str, ...] = field(default=(), repr=False)
    import_output_dir: Path | None = None
    append_imported_records: bool = False


@dataclass(frozen=True)
class ProfilerCapabilities:
    tool: str
    collection_available: bool
    report_import_available: bool
    report_formats: tuple[str, ...]
    supported_backends: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    collection_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive({
            "tool": self.tool,
            "collection_available": self.collection_available,
            "collection_verified": self.collection_verified,
            "report_import_available": self.report_import_available,
            "report_formats": list(self.report_formats),
            "supported_backends": list(self.supported_backends),
            "warnings": list(self.warnings),
        })


@dataclass
class RawProfileArtifact:
    tool: str
    status: str
    mode: str
    collection_id: str
    reason_category: str
    case_dir: str | None
    artifact_manifest: list[dict[str, Any]]
    warnings: list[str]
    results_dir: Path = field(repr=False)
    native_result: Any = field(default=None, repr=False)
    primary_report: str | None = None
    partial_artifact_dir: str | None = None
    target_reports: list[dict[str, Any]] = field(default_factory=list)

    def case_path(self) -> Path | None:
        if self.case_dir is None:
            return None
        root = self.results_dir.resolve()
        path = (root / self.case_dir).resolve()
        path.relative_to(root)
        if len(self.target_reports) > 1:
            return None
        if self.primary_report is not None:
            report_path = (path / self.primary_report).resolve()
            report_path.relative_to(path)
            return report_path
        return path

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive({
            "tool": self.tool, "status": self.status, "mode": self.mode,
            "collection_id": self.collection_id, "reason_category": self.reason_category,
            "case_dir": self.case_dir, "artifact_manifest": self.artifact_manifest,
            "primary_report": self.primary_report, "partial_artifact_dir": self.partial_artifact_dir,
            "target_reports": self.target_reports,
            "warnings": self.warnings,
        })


@runtime_checkable
class ProfilerAdapter(Protocol):
    def capabilities(self, context: ProfileContext | None = None) -> ProfilerCapabilities: ...
    def collect(self, context: ProfileContext) -> RawProfileArtifact: ...
    def parse(self, artifact: RawProfileArtifact) -> list[MetricObservation]: ...
    def normalize(self, observations: list[MetricObservation]) -> ProfilerResult: ...
