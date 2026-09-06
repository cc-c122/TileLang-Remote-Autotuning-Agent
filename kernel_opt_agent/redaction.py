from __future__ import annotations

import os
import re
from typing import Any


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def configured_secret_values(config: Any) -> list[str]:
    values: list[str] = []
    remote = getattr(config, "remote", None)
    llm = getattr(config, "llm", None)
    for env_name in (getattr(remote, "password_env", None), getattr(llm, "api_key_env", None)):
        value = os.environ.get(env_name or "")
        if value:
            values.append(value)
    key_path = getattr(remote, "key_path", None)
    if key_path:
        values.extend([str(key_path), os.path.expanduser(str(key_path))])
    return sorted({value for value in values if value}, key=len, reverse=True)


def redact_text(config: Any, text: str | None) -> str | None:
    if text is None:
        return None
    redacted = text
    for value in configured_secret_values(config):
        redacted = redacted.replace(value, "<redacted:secret>")
    redacted = _BEARER_RE.sub("Bearer <redacted:secret>", redacted)
    redacted = _PRIVATE_KEY_RE.sub("<redacted:private_key>", redacted)
    return redacted


def redact_data(config: Any, value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_data(config, item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(config, item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(config, item) for item in value)
    if isinstance(value, str):
        return redact_text(config, value)
    return value
