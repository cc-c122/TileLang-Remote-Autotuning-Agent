from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You propose TileLang kernel tuning candidates.
Return strict JSON only. Do not include shell commands, secrets, markdown, or prose outside JSON.
Every parameter value must be selected exactly from the provided search_space."""


def build_user_prompt(search_space: dict[str, list[Any]], history: list[dict[str, Any]], count: int) -> str:
    top = [r for r in history if r.get("status") == "benchmark_ok"][:5]
    failed = [r for r in history if r.get("status") != "benchmark_ok"][-10:]
    payload = {
        "search_space": search_space,
        "requested_candidate_count": count,
        "top_results": top,
        "recent_failed_results": failed,
        "output_schema": {
            "candidates": [
                {
                    "config": {k: f"one of search_space.{k}" for k in search_space},
                    "hypothesis": "short text",
                    "expected_improvement": "short text",
                    "risk": "short text",
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=True)

