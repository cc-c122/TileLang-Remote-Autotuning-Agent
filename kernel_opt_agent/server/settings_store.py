from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import SettingsPayload


FORBIDDEN_SECRET_KEYS = {
    "password",
    "api_key",
    "token",
    "private_key",
    "key_content",
    "secret",
}


def _reject_plaintext_secrets(value: Any, path: str = "settings") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_SECRET_KEYS or lowered.endswith("_value"):
                raise ValueError(f"plaintext secret field is not allowed: {path}.{key}")
            _reject_plaintext_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_plaintext_secrets(item, f"{path}[{index}]")


def _redact(settings: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(settings)
    ssh = dict(redacted.get("ssh") or {})
    if ssh.get("key_path"):
        ssh["key_path"] = "<redacted:key_path>"
    ssh["password_env_exists"] = bool(ssh.get("password_env") and os.environ.get(str(ssh.get("password_env"))))
    redacted["ssh"] = ssh
    llm = dict(redacted.get("llm") or {})
    llm["api_key_env_exists"] = bool(llm.get("api_key_env") and os.environ.get(str(llm.get("api_key_env"))))
    redacted["llm"] = llm
    return redacted


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, payload: SettingsPayload) -> dict[str, Any]:
        data = payload.model_dump()
        _reject_plaintext_secrets(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return _redact(data)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _redact(SettingsPayload().model_dump())
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        _reject_plaintext_secrets(raw)
        settings = SettingsPayload.model_validate(raw)
        return _redact(settings.model_dump())

    def load_raw(self) -> SettingsPayload:
        if not self.path.exists():
            return SettingsPayload()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        _reject_plaintext_secrets(raw)
        return SettingsPayload.model_validate(raw)
