from __future__ import annotations

import inspect
import json
import re
import secrets
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from kernel_opt_agent.profiler.base import redact_sensitive
from kernel_opt_agent.runner.command_guard import validate


INSTALL_ROOTS = (
    "/opt/maca/tools", "/opt/metax", "/opt/mcProfiler", "/usr/local/mcProfiler",
    "/app/mcProfiler", "/data/mcProfiler", "/workspace/tools",
    "/tmp/kernel_agent_profiler_collection/tools",
)
RESULT_PREFIX = "MCPROFILER_DISCOVERY_RESULT "


def validate_tool_path(value: str, tool_name: str) -> str:
    import posixpath
    import re

    if value == tool_name:
        return value
    if (not isinstance(value, str) or len(value) > 1024
            or not re.fullmatch(r"/[A-Za-z0-9_./ -]+", value)
            or any(part in {".", "..", "", ".ssh", ".gnupg", ".aws"} for part in value.split("/")[1:])
            or posixpath.normpath(value) != value
            or posixpath.basename(value) not in {tool_name, tool_name + ".bin"}):
        raise ValueError("profiler tool must be its command name or a safe absolute executable path")
    if any(value == root or value.startswith(root + "/")
           for root in ("/etc", "/proc", "/sys", "/dev", "/boot", "/root/.ssh")):
        raise ValueError("profiler tool path is outside allowed executable locations")
    return value


class McProfilerCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    status: Literal["available", "unavailable", "failed", "cancelled", "not_checked"] = "not_checked"
    version: str | None = None
    server_version: str | None = None
    discovery_source: Literal["explicit", "path", "allowlist", "none"] = "none"
    executable: str | None = None
    server_executable: str | None = None
    server_mode: Literal["embedded", "managed_external"] = "embedded"
    reason_category: str = "not_checked"
    warnings: list[str] = Field(default_factory=list)
    logs: dict[str, str] = Field(default_factory=dict)
    checked_at: str | None = None


def _probe_tools(payload: dict) -> dict:
    import itertools
    import os
    import re
    import shutil
    import subprocess
    import time
    from pathlib import Path

    warnings = []
    deadline = time.monotonic() + payload["timeout_seconds"]
    result = {"available": False, "status": "unavailable", "reason_category": "client_unavailable",
              "discovery_source": "none", "warnings": warnings}
    workspace = Path.cwd().resolve()
    home = (workspace / ".kernel_opt_agent/tool_discovery/home").resolve()
    home.relative_to(workspace)
    home.mkdir(parents=True, exist_ok=True)

    def usable(raw, tool, root=None):
        if not raw:
            return None
        try:
            validate_tool_path(str(raw), tool)
            path = Path(raw).resolve()
            validate_tool_path(str(path), tool)
            if root is not None:
                path.relative_to(root.resolve())
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        except (ValueError, OSError, RuntimeError):
            return None

    def candidates():
        explicit = payload.get("executable")
        if explicit and explicit != "mcProfiler":
            yield explicit, "explicit", None
        yield shutil.which("mcProfiler"), "path", None
        visits = 0
        visited = set()
        for root_text in payload["roots"]:
            root = Path(root_text)
            queue = [(root, 0)]
            while queue and visits < 32:
                base, depth = queue.pop(0)
                try:
                    canonical = base.resolve()
                except (OSError, RuntimeError):
                    continue
                if str(canonical) == "/" or any(str(canonical) == bad or str(canonical).startswith(bad + "/")
                                                for bad in ("/etc", "/proc", "/sys", "/dev", "/boot")):
                    continue
                if canonical in visited:
                    continue
                visited.add(canonical)
                visits += 1
                yield base / "mcProfiler", "allowlist", root
                if depth >= 3:
                    continue
                for name in ("bin", "tools", "backend", "mcProfiler-ubuntu18", "mcProfiler-ubuntu22"):
                    child = base / name
                    try:
                        child.resolve().relative_to(root.resolve())
                        if child.is_dir():
                            queue.append((child, depth + 1))
                    except (ValueError, OSError, RuntimeError):
                        continue
                try:
                    with os.scandir(base) as entries:
                        bounded = list(itertools.islice(entries, 33))
                    if len(bounded) > 32:
                        warnings.append("allowlist directory entry limit reached")
                    for item in sorted(bounded[:32], key=lambda entry: entry.name):
                        if re.fullmatch(r"(?:mcprofiler|linux-perf-kit)[-_.0-9A-Za-z]*", item.name, re.IGNORECASE) and item.is_dir(follow_symlinks=False):
                            queue.append((Path(item.path), depth + 1))
                except OSError:
                    pass

    seen = set()
    for raw, source, root in candidates():
        if time.monotonic() >= deadline:
            result["reason_category"] = "timeout"
            break
        client = usable(raw, "mcProfiler", root)
        if not client or client in seen:
            if source == "explicit":
                warnings.append("explicit client unavailable; trying lower-priority locations")
            continue
        seen.add(client)
        server_override = payload.get("server_executable")
        server = usable(server_override, "profiler_server") if server_override != "profiler_server" else None
        if server_override != "profiler_server" and not server:
            warnings.append("explicit server unavailable; trying bundled/PATH server")
        if not server:
            server = usable(Path(client).parent / "profiler_server", "profiler_server")
        if not server:
            server = usable(Path(client).parent / "backend/profiler_server", "profiler_server")
        if not server:
            server = usable(shutil.which("profiler_server"), "profiler_server")
        if not server:
            result["reason_category"] = "server_unavailable"
            continue
        # Inherit the selected device verbatim, never declare a task override.
        visibility_names = {prefix + "_VISIBLE_DEVICES" for prefix in ("CUDA", "MACA", "HIP", "MUSA")}
        environment = {name: value for name, value in os.environ.items()
                       if name in {"PATH", "LD_LIBRARY_PATH", "MACA_PATH"} or name in visibility_names}
        environment.update(HOME=str(home), TMPDIR=str(home), XDG_CACHE_HOME=str(home))
        try:
            # The enclosing SSH managed process group owns timeout/cancellation,
            # including any children of a bundled/PyInstaller executable.
            version = subprocess.run([client, "version"], capture_output=True, text=True, env=environment)
            matched = re.search(r"\bversion\s*[:=]\s*(\d+(?:\.\d+){1,4})", version.stdout, re.IGNORECASE)
            if version.returncode != 0 or matched is None:
                result["reason_category"] = "version_unrecognized"
                continue
        except (OSError, UnicodeError):
            result["reason_category"] = "version_failed"
            continue
        return {"available": True, "status": "available", "reason_category": "none", "executable": client,
                "server_executable": server, "version": matched.group(1), "server_version": None,
                "discovery_source": source,
                "server_mode": "embedded" if Path(client).parent == Path(server).parent else "managed_external",
                "warnings": list(dict.fromkeys(warnings))[:15] +
                    ["server component is executable; an independent server version is not reported"]}
    result["warnings"] = list(dict.fromkeys(warnings))[:15] + ["continuing with benchmark/log evidence"]
    if result["reason_category"] not in {"client_unavailable", "server_unavailable"}:
        result["status"] = "failed"
    return result


def discover_mcprofiler(
    runner: Any,
    executable: str = "mcProfiler",
    server_executable: str = "profiler_server",
    *,
    timeout_seconds: int = 20,
    should_cancel: Callable[[], bool] | None = None,
    results_dir: Path | None = None,
    redactor: Callable[[Any], Any] | None = None,
) -> McProfilerCapability:
    capability = McProfilerCapability(checked_at=datetime.now(timezone.utc).isoformat())
    clean = redactor or redact_sensitive

    def finish(status, reason):
        capability.status = status
        capability.reason_category = reason
        capability.available = status == "available"
        return capability

    if should_cancel and should_cancel():
        return finish("cancelled", "cancelled")
    workspace = getattr(getattr(runner, "info", None), "remote_workspace", None)
    if not isinstance(workspace, str) or not workspace or not hasattr(runner, "run_cancellable"):
        return finish("unavailable", "runner_unsupported")
    try:
        validate_tool_path(executable, "mcProfiler")
        validate_tool_path(server_executable, "profiler_server")
    except ValueError:
        return finish("failed", "unsafe_tool_path")
    timeout_seconds = max(1, min(timeout_seconds, 20))
    payload = {"executable": executable, "server_executable": server_executable,
               "timeout_seconds": max(0.5, timeout_seconds - 1), "roots": INSTALL_ROOTS}
    script = ("import json\n" + inspect.getsource(validate_tool_path) + "\n" + inspect.getsource(_probe_tools)
              + "\nprint(" + repr(RESULT_PREFIX) + "+json.dumps(_probe_tools(" + repr(payload) + "),sort_keys=True))")
    command = "python3 -B -c " + shlex.quote(script)
    guard = validate(command, workspace, workspace, getattr(runner, "denied_commands", None))
    if not guard.allowed:
        return finish("failed", "guard_denied")
    try:
        execution = runner.run_cancellable("mcprofiler_discovery", command, should_cancel, timeout_seconds=timeout_seconds)
    except Exception:
        return finish("failed", "runner_exception")
    if results_dir is not None:
        log_id = "discovery-" + secrets.token_hex(8)
        directory = results_dir / "tool_discovery"
        directory.resolve().relative_to(results_dir.resolve())
        directory.mkdir(parents=True, exist_ok=True)
        for stream in ("stdout", "stderr"):
            path = directory / (log_id + "." + stream + ".log")
            path.write_text(str(clean(getattr(execution, stream))), encoding="utf-8")
            capability.logs[stream] = path.relative_to(results_dir).as_posix()
    if execution.guard_denied:
        return finish("failed", "guard_denied")
    if "stop could not be verified" in (execution.error_message or ""):
        return finish("failed", "cleanup_unverified")
    if execution.returncode == 130 and (should_cancel is None or should_cancel()):
        return finish("cancelled", "cancelled")
    if execution.timeout:
        return finish("failed", "timeout")
    if execution.returncode != 0:
        return finish("failed", "query_failed")
    if should_cancel and should_cancel():
        return finish("cancelled", "cancelled")
    try:
        line = next(line for line in execution.stdout.splitlines() if line.startswith(RESULT_PREFIX))
        raw = clean(json.loads(line[len(RESULT_PREFIX):]))
        if not isinstance(raw, dict) or not {"available", "status", "reason_category", "discovery_source"}.issubset(raw):
            raise ValueError("incomplete capability")
        parsed = McProfilerCapability.model_validate(raw)
        if parsed.available != (parsed.status == "available"):
            raise ValueError("inconsistent capability")
        if parsed.available:
            validate_tool_path(parsed.executable, "mcProfiler")
            validate_tool_path(parsed.server_executable, "profiler_server")
            if parsed.status != "available" or not parsed.version or not re.fullmatch(r"\d+(?:\.\d+){1,4}", parsed.version):
                raise ValueError("invalid capability")
        parsed.checked_at = capability.checked_at
        parsed.logs = capability.logs
        return parsed
    except (ValueError, StopIteration, TypeError):
        return finish("failed", "invalid_discovery_result")


def persist_capability(results_dir: Path, capability: McProfilerCapability, redactor=None) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    data = (redactor or redact_sensitive)({"mcprofiler": capability.model_dump(mode="json")})
    path = results_dir / "profiler_capabilities.json"
    path.resolve().relative_to(results_dir.resolve())
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
