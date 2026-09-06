from __future__ import annotations

import subprocess
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .command_guard import validate


@dataclass
class CommandResult:
    name: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    start_time: float
    end_time: float
    duration: float
    timeout: bool = False
    guard_denied: bool = False
    error_message: str | None = None


class LocalRunner:
    def __init__(self, workspace: Path, timeout_seconds: int, denied_commands: list[str] | None = None):
        self.workspace = workspace.resolve()
        self.timeout_seconds = timeout_seconds
        self.denied_commands = denied_commands or []
        self.workspace.mkdir(parents=True, exist_ok=True)

    def file_sha256(self, relative_path: str) -> str:
        path = (self.workspace / relative_path).resolve()
        path.relative_to(self.workspace)
        if not path.is_file():
            raise ValueError(f"execution file not found: {relative_path}")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run(self, name: str, command: str | None) -> CommandResult:
        start = time.time()
        if not command:
            return CommandResult(name, "", 0, "", "", start, start, 0.0)
        guard = validate(command, self.workspace, self.workspace, self.denied_commands)
        if not guard.allowed:
            end = time.time()
            return CommandResult(name, command, 126, "", guard.reason, start, end, end - start, guard_denied=True, error_message=guard.reason)
        try:
            proc = subprocess.run(
                command,
                cwd=self.workspace,
                shell=True,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
            end = time.time()
            return CommandResult(name, command, proc.returncode, proc.stdout, proc.stderr, start, end, end - start)
        except subprocess.TimeoutExpired as exc:
            end = time.time()
            return CommandResult(
                name,
                command,
                124,
                exc.stdout or "",
                exc.stderr or "",
                start,
                end,
                end - start,
                timeout=True,
                error_message=f"command timed out after {self.timeout_seconds}s",
            )
        except Exception as exc:
            end = time.time()
            return CommandResult(name, command, 1, "", str(exc), start, end, end - start, error_message=str(exc))

