from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "hardware_profiles"


def normalize_profile_name(name: str | None) -> str:
    if not name:
        return "unknown_gpu"
    return name.strip().lower().replace("-", "_").replace(" ", "_")


class HardwareProfileLoader:
    def __init__(self, profile_dir: Path | None = None):
        self.profile_dir = profile_dir or PROFILE_ROOT

    def load(self, profile_name: str | None) -> tuple[str, dict[str, Any]]:
        normalized = normalize_profile_name(profile_name)
        path = self.profile_dir / f"{normalized}.yaml"
        if not path.exists() and normalized != "unknown_gpu":
            normalized = "unknown_gpu"
            path = self.profile_dir / "unknown_gpu.yaml"
        if not path.exists():
            raise FileNotFoundError(f"hardware profile not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if data.get("source") != "builtin_profile":
            raise ValueError(f"hardware profile must declare source: builtin_profile: {path}")
        return normalized, data
