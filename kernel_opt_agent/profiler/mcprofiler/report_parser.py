from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel_opt_agent.profiler.base import MetricObservation

from .metric_mapping import MAPPING_BY_SOURCE
from .report_discovery import McProfilerCase, discover_case


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


@dataclass
class McProfilerParseResult:
    metadata: dict[str, Any] = field(default_factory=dict)
    observations: list[MetricObservation] = field(default_factory=list)
    unknown_rows: list[dict[str, Any]] = field(default_factory=list)
    raw_sections: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": _redact_sensitive(self.metadata),
            "observations": _redact_sensitive([item.to_dict() for item in self.observations]),
            "unknown_rows": _redact_sensitive(self.unknown_rows),
            "raw_sections": _redact_sensitive(self.raw_sections),
            "artifacts": _redact_sensitive(self.artifacts),
            "warnings": self.warnings,
        }

    def metric_map(self) -> dict[str, MetricObservation]:
        return {item.metric_name: item for item in self.observations}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_readme_metadata(readme_path: Path | None) -> dict[str, Any]:
    if not readme_path or not readme_path.exists():
        return {}
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    metadata: dict[str, Any] = {}
    patterns = {
        "case_name": r"Case Name:\s*`([^`]+)`",
        "exec_id": r"Exec ID:\s*`([^`]+)`",
        "start_time": r"开始时间:\s*`([^`]+)`",
        "finish_time": r"完成时间:\s*`([^`]+)`",
        "gpu": r"GPU:\s*([^\n]+)",
        "target_subkernel": r"被测子核:\s*([^\n]+)",
        "source_file": r"被测源码:\s*`([^`]+)`",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            metadata[key] = match.group(1).strip()
    version_match = re.search(r"采集环境:\s*([^，\n]+)", text)
    if version_match:
        metadata["maca_version"] = version_match.group(1).strip()
    shape_match = re.search(r"Shape:\s*([^\n]+)", text)
    if shape_match:
        metadata["shape"] = _parse_shape(shape_match.group(1))
    return metadata


def _extract_manifest_metadata(manifest_path: Path | None) -> dict[str, Any]:
    if not manifest_path or not manifest_path.exists():
        return {}
    try:
        manifest = _read_json(manifest_path)
    except Exception:
        return {}
    if not isinstance(manifest, dict):
        return {}
    return {
        key: manifest[key]
        for key in ("case_name", "exec_id", "maca_version", "target_subkernel", "shape")
        if key in manifest
    }


def _parse_shape(text: str) -> dict[str, int | str]:
    shape: dict[str, int | str] = {}
    for part in text.split(","):
        if "=" not in part:
            continue
        key, value = [item.strip() for item in part.split("=", 1)]
        try:
            shape[key] = int(value)
        except ValueError:
            shape[key] = value
    return shape


def _load_sha_manifest(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    manifest: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            manifest[parts[1].strip()] = parts[0].strip()
    return manifest


def _contains_sensitive_value(data: Any) -> bool:
    lowered = json.dumps(data, ensure_ascii=False, default=str).lower()
    return any(pattern in lowered for pattern in SENSITIVE_PATTERNS)


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(pattern in lowered for pattern in SENSITIVE_PATTERNS)


def _is_sensitive_string(value: str) -> bool:
    lowered = value.lower()
    return any(pattern in lowered for pattern in SENSITIVE_PATTERNS)


def _redact_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if _is_sensitive_key(key):
                redacted[str(key)] = REDACTED_SECRET
            else:
                redacted[str(key)] = _redact_sensitive(value)
        return redacted
    if isinstance(data, list):
        return [_redact_sensitive(item) for item in data]
    if isinstance(data, tuple):
        return tuple(_redact_sensitive(item) for item in data)
    if isinstance(data, str) and _is_sensitive_string(data):
        return REDACTED_SECRET
    return data


def redact_mcprofiler_sensitive(data: Any) -> Any:
    return _redact_sensitive(data)


def _artifact_id(metadata: dict[str, Any], case: McProfilerCase, report_sha256: str) -> str:
    case_id = metadata.get("case_name") or metadata.get("exec_id") or case.case_root.name
    return f"{case_id}:{report_sha256[:16]}"


def parse_mcprofiler_case(path: str | Path) -> McProfilerParseResult:
    return parse_discovered_case(discover_case(path))


def parse_discovered_case(case: McProfilerCase) -> McProfilerParseResult:
    warnings: list[str] = []
    metadata = _extract_manifest_metadata(case.manifest_path)
    metadata.update(_extract_readme_metadata(case.readme_path))
    artifacts: dict[str, Any] = {
        "case_root_name": case.case_root.name,
        "report_dumped_result": None,
        "sha256_manifest": _load_sha_manifest(case.sha256sums_path),
    }
    if case.manifest_path:
        try:
            artifacts["fixture_manifest"] = _redact_sensitive(_read_json(case.manifest_path))
        except Exception as exc:
            warnings.append(f"failed to parse fixture manifest: {exc}")
    if case.report_path is None:
        warnings.append("report_dumped_result.json not found")
        return McProfilerParseResult(metadata=metadata, artifacts=artifacts, warnings=warnings)
    try:
        report = _read_json(case.report_path)
    except Exception as exc:
        warnings.append(f"failed to parse report_dumped_result.json: {exc}")
        return McProfilerParseResult(metadata=metadata, artifacts=artifacts, warnings=warnings)
    artifacts["report_dumped_result"] = {
        "file_name": case.report_path.name,
        "sha256": _sha256(case.report_path),
    }
    artifacts["artifact_id"] = _artifact_id(metadata, case, artifacts["report_dumped_result"]["sha256"])
    expected = artifacts["sha256_manifest"].get("case_report/report_dumped_result.json")
    if expected and expected != artifacts["report_dumped_result"]["sha256"]:
        warnings.append("report_dumped_result.json sha256 does not match SHA256SUMS.txt")
    if _contains_sensitive_value(report):
        warnings.append("report contains sensitive-looking field names; values were not promoted")

    observations: list[MetricObservation] = []
    unknown_rows: list[dict[str, Any]] = []
    if not isinstance(report, dict):
        warnings.append("report_dumped_result.json root is not an object")
        return McProfilerParseResult(metadata=metadata, artifacts=artifacts, warnings=warnings)
    for section, rows in report.items():
        if not isinstance(rows, list):
            unknown_rows.append(_redact_sensitive({"section": section, "name": None, "value": rows, "reason": "section is not a row list"}))
            continue
        for row in rows:
            if not isinstance(row, dict):
                unknown_rows.append(_redact_sensitive({"section": section, "name": None, "value": row, "reason": "row is not an object"}))
                continue
            source_field = row.get("name")
            value = row.get("data")
            mapping = MAPPING_BY_SOURCE.get((str(section), str(source_field)))
            if mapping is None:
                unknown_rows.append(_redact_sensitive({"section": section, "name": source_field, "value": value}))
                continue
            observations.append(
                MetricObservation(
                    metric_name=mapping.metric_name,
                    source_field_name=_redact_sensitive(str(source_field)),
                    value=_redact_sensitive(value),
                    unit=mapping.unit,
                    source=_redact_sensitive("mcprofiler"),
                    available=value is not None and not bool(row.get("isError")),
                    confidence=mapping.confidence,
                    artifact=_redact_sensitive(artifacts["artifact_id"]),
                    parse_warnings=_redact_sensitive(
                        list(mapping.warnings) + ([str(row.get("message"))] if row.get("message") else [])
                    ),
                )
            )
    return McProfilerParseResult(
        metadata=metadata,
        observations=observations,
        unknown_rows=unknown_rows,
        raw_sections=_redact_sensitive(report),
        artifacts=artifacts,
        warnings=warnings,
    )
