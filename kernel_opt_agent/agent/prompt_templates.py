from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You propose TileLang kernel tuning candidates.
Return strict JSON only. Do not include shell commands, secrets, markdown, or prose outside JSON.
Every parameter value must be selected exactly from the provided search_space."""


def build_user_prompt(
    search_space: dict[str, list[Any]],
    history: list[dict[str, Any]],
    count: int,
    safe_probe_results: list[dict[str, Any]] | None = None,
    hardware_info: Any | None = None,
) -> str:
    top = [r for r in history if r.get("status") == "benchmark_ok"][:5]
    failed = [r for r in history if r.get("status") != "benchmark_ok"][-10:]
    probe_results = safe_probe_results or []
    unavailable = [
        {
            "param_name": r.get("param_name"),
            "candidate_value": r.get("candidate_value"),
            "status": r.get("status"),
            "inference": r.get("inference"),
            "confidence": r.get("confidence"),
        }
        for r in probe_results
        if r.get("status") in {"failed", "timeout", "guard_denied", "exception"}
    ]
    hardware_payload = hardware_info.to_dict() if hardware_info is not None and hasattr(hardware_info, "to_dict") else None
    payload = {
        "search_space": search_space,
        "requested_candidate_count": count,
        "hardware_info": hardware_payload,
        "safe_probe_context": {
            "note": "Safe probe is a low/medium-confidence availability check, not an official hardware limit. Prefer values that passed and avoid clearly unavailable values, while still selecting only from search_space.",
            "results": probe_results,
            "unavailable_values": unavailable,
        },
        "top_results": top,
        "recent_failed_results": failed,
        "output_schema": {
            "candidates": [
                {
                    "config": {k: f"one of search_space.{k}" for k in search_space},
                    "hypothesis": "short text",
                    "expected_improvement": "short text",
                    "risk": "short text",
                    "hardware_assumptions": {"known_field_name": "known value or unknown"},
                    "confidence": "low | medium | high | unknown",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=True)
