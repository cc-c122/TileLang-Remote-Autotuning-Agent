from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PATCH_TRIAL_STATUSES = {
    "proposed",
    "applied",
    "validation_failed",
    "syntax_failed",
    "build_failed",
    "correctness_failed",
    "benchmark_failed",
    "benchmark_ok",
    "benchmark_regressed",
    "rolled_back",
}


@dataclass(frozen=True)
class PatchTrial:
    trial_id: str
    patch_id: str
    optimization_name: str
    target_region: str
    hypothesis: str
    expected_improvement: str
    risk: str
    status: str = "proposed"
    diagnosis_refs: list[str] = field(default_factory=list)
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    improvement: float | None = None
    rollback_available: bool = True
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PATCH_TRIAL_STATUSES:
            raise ValueError(f"unsupported patch trial status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "patch_id": self.patch_id,
            "optimization_name": self.optimization_name,
            "target_region": self.target_region,
            "hypothesis": self.hypothesis,
            "expected_improvement": self.expected_improvement,
            "risk": self.risk,
            "status": self.status,
            "diagnosis_refs": self.diagnosis_refs,
            "metrics_before": self.metrics_before,
            "metrics_after": self.metrics_after,
            "improvement": self.improvement,
            "rollback_available": self.rollback_available,
            "artifacts": self.artifacts,
            "error": self.error,
        }


def write_patch_trials_jsonl(path: Path, trials: list[PatchTrial]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for trial in trials:
            handle.write(json.dumps(trial.to_dict(), ensure_ascii=False, default=str) + "\n")
    return path
