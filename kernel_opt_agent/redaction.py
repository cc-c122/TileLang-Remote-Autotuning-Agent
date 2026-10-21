from __future__ import annotations

import os
import re
import logging
import threading
from collections import Counter
from typing import Any


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_LOG_SECRETS: Counter[str] = Counter()
_LOG_SECRETS_LOCK = threading.RLock()


def register_log_secrets(values: list[str]) -> None:
    with _LOG_SECRETS_LOCK:
        _LOG_SECRETS.update(value for value in values if value)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "paramiko", "httpx"):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(item, SessionLogFilter) for item in handler.filters):
                handler.addFilter(SessionLogFilter())


def unregister_log_secrets(values: list[str]) -> None:
    with _LOG_SECRETS_LOCK:
        for value in values:
            _LOG_SECRETS[value] -= 1
            if _LOG_SECRETS[value] <= 0:
                del _LOG_SECRETS[value]


class SessionLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        with _LOG_SECRETS_LOCK:
            for value in sorted(_LOG_SECRETS, key=len, reverse=True):
                message = message.replace(value, "<redacted:secret>")
        message = re.sub(r"(/api/ssh/sessions/)[^\s?\"']+", r"\1<redacted:session>", message)
        record.msg = redact_text(None, message)
        record.args = ()
        if record.exc_info:
            record.exc_info = None
            record.exc_text = "<redacted:exception>"
        return True


def configured_secret_values(config: Any) -> list[str]:
    values: list[str] = []
    from kernel_opt_agent.runner.ssh_runtime import current_ssh_context

    context = current_ssh_context()
    if context is not None:
        values.extend(context.secret_values())
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
