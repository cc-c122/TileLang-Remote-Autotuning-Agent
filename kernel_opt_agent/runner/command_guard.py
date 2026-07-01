from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DENIED_COMMANDS = [
    "rm -rf",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "apt remove",
    "apt purge",
    "yum remove",
    "curl | sh",
    "wget | sh",
    "curl -fsSL",
    "chmod 777 /",
]

SYSTEM_OVERWRITE_RE = re.compile(r">\s*(/etc/|/usr/|/bin/|/lib/|/sbin/|/boot/)")
DESTRUCTIVE_RE = re.compile(r"\b(rm|mv|cp|truncate)\b")


@dataclass
class GuardResult:
    allowed: bool
    reason: str = "ok"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def validate(command: str, cwd: str | Path, workspace: str | Path, denied_commands: list[str] | None = None) -> GuardResult:
    if not command or not command.strip():
        return GuardResult(False, "empty command")
    workspace_path = Path(workspace).resolve()
    cwd_path = Path(cwd).resolve()
    try:
        cwd_path.relative_to(workspace_path)
    except ValueError:
        return GuardResult(False, f"cwd is outside workspace: {cwd_path}")

    lowered = _norm(command)
    denied = DEFAULT_DENIED_COMMANDS + list(denied_commands or [])
    for fragment in denied:
        if fragment and _norm(fragment) in lowered:
            return GuardResult(False, f"dangerous command fragment denied: {fragment}")
    if SYSTEM_OVERWRITE_RE.search(command):
        return GuardResult(False, "redirecting output into system paths is denied")
    if re.search(r"\bcd\s+(\.\.|/|~)", lowered):
        return GuardResult(False, "cd outside workspace is denied")
    if DESTRUCTIVE_RE.search(lowered) and re.search(r"(\.\.|/etc/|/usr/|/bin/|/lib/|/sbin/|/boot/|~)", lowered):
        return GuardResult(False, "destructive operation outside workspace is denied")
    return GuardResult(True)

