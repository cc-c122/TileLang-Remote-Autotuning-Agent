from __future__ import annotations

import json
from typing import Any

from kernel_opt_agent.runner.local_runner import CommandResult

from .detection_commands import GENERIC_LINUX_COMMANDS


class GenericLinuxDetector:
    def __init__(self, runner: Any):
        self.runner = runner

    def detect(self) -> tuple[dict[str, Any], list[str]]:
        detected: dict[str, Any] = {}
        log_lines: list[str] = []
        for detection_command in GENERIC_LINUX_COMMANDS:
            try:
                result: CommandResult = self.runner.run(f"hardware_{detection_command.name}", detection_command.command)
            except Exception as exc:
                log_lines.append(f"remote detection command failed: {detection_command.name}: {exc}")
                continue
            if result.returncode != 0:
                reason = result.error_message or result.stderr.strip() or f"return code {result.returncode}"
                log_lines.append(f"remote detection command failed: {detection_command.name}: {reason}")
                continue
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError) as exc:
                log_lines.append(f"remote detection parse failed: {detection_command.name}: {exc}")
                continue
            for field_name, value in payload.items():
                if value is not None and value != "":
                    detected[field_name] = value
            log_lines.append(f"remote detection command succeeded: {detection_command.name}")
        return detected, log_lines
