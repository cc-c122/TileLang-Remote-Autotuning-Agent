from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionCommand:
    name: str
    command: str


GENERIC_LINUX_COMMANDS = [
    DetectionCommand(
        "python_platform",
        "python -c \"import json, platform, sys; print(json.dumps({'python_version': platform.python_version(), 'platform': platform.platform(), 'os': sys.platform}))\"",
    ),
    DetectionCommand(
        "tilelang_version",
        "python -c \"import json; import tilelang; print(json.dumps({'tilelang_version': getattr(tilelang, '__version__', 'unknown')}))\"",
    ),
    DetectionCommand(
        "mctilelang_version",
        "python -c \"import json; import mctilelang; print(json.dumps({'mctilelang_version': getattr(mctilelang, '__version__', 'unknown')}))\"",
    ),
    DetectionCommand(
        "compiler_version",
        "python -c \"import json, shutil, subprocess; cc=shutil.which('gcc') or shutil.which('cc'); print(json.dumps({'compiler_version': subprocess.run([cc, '--version'], text=True, capture_output=True, timeout=5).stdout.splitlines()[0] if cc else None}))\"",
    ),
]


READ_ONLY_COMMAND_NAMES = {command.name for command in GENERIC_LINUX_COMMANDS}
