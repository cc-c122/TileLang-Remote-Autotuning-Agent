from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProfilerResult:
    available: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw_output_path: str | None = None


class BaseProfiler:
    def collect(self, run_context: dict[str, Any]) -> ProfilerResult:
        raise NotImplementedError

