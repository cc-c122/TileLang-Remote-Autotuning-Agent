from __future__ import annotations

from typing import Any


def objective_value(metrics: dict[str, Any], objective: str) -> float | None:
    value = metrics.get(objective)
    return value if isinstance(value, (int, float)) else None


def is_better(candidate: float | None, incumbent: float | None, objective: str) -> bool:
    if candidate is None:
        return False
    if incumbent is None:
        return True
    return candidate < incumbent if objective == "latency" else candidate > incumbent

