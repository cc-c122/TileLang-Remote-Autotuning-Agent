from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _object(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_report_context(root: Path, metadata: dict[str, Any], kernel_name: str | None) -> dict[str, Any]:
    config = _object(root / "mcProfiler.json")
    conditions = config.get("conditions")
    conditions = conditions if isinstance(conditions, dict) else {}
    agent = _object(root / "agent_collection_context.json")
    version = agent.get("tool_version") or metadata.get("mcprofiler_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,4}", version):
        version = None
    device = agent.get("device_id")
    if not isinstance(device, str) or not re.fullmatch(r"device_sha256:[0-9a-f]{64}", device):
        device = None
    scope = {
        "report_layout": "named_kernel" if kernel_name else "aggregate",
        "counter_scope": "unknown",
        "requested_kernel": agent.get("requested_kernel"),
        "configured_kernels": conditions.get("kernelnames"),
        "per_kernel_actual": conditions.get("is_perkernel"),
        "profile_from_start_actual": conditions.get("profile_from_start"),
        "counts_actual": conditions.get("counts"),
        "background_workload_excluded": None,
        "device_binding": "runner_visible_devices_request" if device else "unknown",
        "collection_window": agent.get("collection_window"),
        "source_sha256": agent.get("source_sha256"),
        "report_sha256": agent.get("report_sha256"),
    }
    return {"device_id": device, "tool_version": version, "collection_scope": scope}
