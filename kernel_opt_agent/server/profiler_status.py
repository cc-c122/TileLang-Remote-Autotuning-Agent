from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"parse_error": True})
    return rows


def has_available_evidence(evidence_rows: list[dict[str, Any]]) -> bool:
    return any(item.get("available") is True for item in evidence_rows)


def profiler_status(profiler_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> str:
    if has_available_evidence(evidence_rows):
        return "profiler_metrics_available"
    if not profiler_rows:
        return "disabled"
    enabled_rows = [item for item in profiler_rows if item.get("profiler", {}).get("enabled")]
    if not enabled_rows:
        return "disabled"
    if any(item.get("profiler", {}).get("error") for item in enabled_rows):
        return "collection_failed"
    benchmark_fields = {"latency_ms", "tflops", "estimated_hbm_bandwidth"}
    for item in enabled_rows:
        result = item.get("profiler", {}).get("result") or {}
        available = result.get("available_metrics") or {}
        if any(available.get(name) for name in benchmark_fields):
            return "benchmark_only"
    return "collection_failed"
