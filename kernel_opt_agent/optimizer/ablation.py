from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AblationResult:
    trial_id: str
    patch_ids: list[str]
    status: str
    objective: str
    baseline_value: float | None = None
    patched_value: float | None = None
    improvement: float | None = None
    reason: str | None = None
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "patch_ids": self.patch_ids,
            "status": self.status,
            "objective": self.objective,
            "baseline_value": self.baseline_value,
            "patched_value": self.patched_value,
            "improvement": self.improvement,
            "reason": self.reason,
            "metrics_before": self.metrics_before,
            "metrics_after": self.metrics_after,
            "artifacts": self.artifacts,
        }


def write_ablation_jsonl(path: Path, results: list[AblationResult]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")
    return path


def write_ablation_csv(path: Path, results: list[AblationResult]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["trial_id", "patch_ids", "status", "objective", "baseline_value", "patched_value", "improvement", "reason"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            row = result.to_dict()
            row["patch_ids"] = ",".join(result.patch_ids)
            writer.writerow({field: row.get(field) for field in fields})
    return path
