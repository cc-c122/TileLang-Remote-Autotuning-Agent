from __future__ import annotations

import hashlib
import json
import posixpath
import re
import secrets
import shlex
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from kernel_opt_agent.profiler.base import redact_sensitive
from kernel_opt_agent.runner.command_guard import validate


COLLECTION_SCHEMA_VERSION = "v2.mcprofiler_collection.v1"
CollectionStatus = Literal["collected", "imported", "unsupported", "failed", "cancelled"]
COLLECTION_STATUSES = {"collected", "imported", "unsupported", "failed", "cancelled"}
DEFAULT_METRICS = (
    "Global Read Instructions",
    "Global Write Instructions",
    "Private Read Instructions",
    "Private Write Instructions",
    "VL1 Hit Rate",
    "L2C Hit Rate",
    "Dnoc Read Average Latency",
    "shared memory access efficiency",
    "average conflict cycles per instruction",
    "Achieved waves",
    "Dispatched waves",
    "AP MMA Duty ratio",
)
DEFAULT_MAX_ARTIFACT_FILES = 2048
DEFAULT_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_SAFE_EXECUTABLE = re.compile(r"^[A-Za-z0-9_./-]+$")
_SAFE_CASE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class McProfilerCollectionRequest:
    target_command: str
    case_name: str
    source_trial_id: str
    source_sha256: str
    shape_id: str | None = None
    metrics: tuple[str, ...] = DEFAULT_METRICS
    kernel_names: tuple[str, ...] = ()
    executable: str = "mcProfiler"
    server_executable: str = "profiler_server"
    service_port: int | None = None
    startup_wait_seconds: float = 2.0
    timeout_seconds: int = 120
    max_artifact_files: int = DEFAULT_MAX_ARTIFACT_FILES
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if not self.target_command.strip():
            raise ValueError("mcProfiler target command is required")
        if self.case_name in {".", ".."} or not _SAFE_CASE_NAME.fullmatch(self.case_name):
            raise ValueError("mcProfiler case_name must be a non-dot name containing only letters, numbers, dot, underscore, or dash")
        if not self.source_trial_id.strip():
            raise ValueError("mcProfiler source_trial_id is required")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("mcProfiler source_sha256 must be a lowercase SHA-256 digest")
        if self.shape_id is not None and (not self.shape_id.strip() or any(char in self.shape_id for char in "\r\n\0")):
            raise ValueError("mcProfiler shape_id must be null or non-empty single-line text")
        for label, executable in (("executable", self.executable), ("server_executable", self.server_executable)):
            if not _SAFE_EXECUTABLE.fullmatch(executable):
                raise ValueError(f"mcProfiler {label} contains unsupported characters")
        if self.service_port is not None and not 1024 <= self.service_port <= 65535:
            raise ValueError("mcProfiler service_port must be between 1024 and 65535")
        if self.startup_wait_seconds <= 0 or self.startup_wait_seconds > 30:
            raise ValueError("mcProfiler startup_wait_seconds must be in (0, 30]")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600:
            raise ValueError("mcProfiler timeout_seconds must be in [1, 3600]")
        if self.max_artifact_files <= 0 or self.max_artifact_bytes <= 0:
            raise ValueError("mcProfiler artifact limits must be positive")
        for value in (*self.metrics, *self.kernel_names):
            if not value or any(character in value for character in "\r\n\0,"):
                raise ValueError("mcProfiler metric and kernel names must be non-empty comma-free text")


@dataclass
class McProfilerCollectionResult:
    status: CollectionStatus
    reason_category: str
    message: str
    case_name: str
    collection_id: str
    source_trial_id: str
    source_sha256: str
    shape_id: str | None
    started_at: str
    finished_at: str
    mode: str = "remote_official_cli"
    schema_version: str = COLLECTION_SCHEMA_VERSION
    metrics_requested: list[str] = field(default_factory=list)
    kernel_names: list[str] = field(default_factory=list)
    execution_manifest: dict[str, Any] = field(default_factory=dict)
    case_dir: str | None = None
    artifact_manifest: list[dict[str, Any]] = field(default_factory=list)
    available_metrics: dict[str, bool] = field(default_factory=dict)
    metric_observations: list[dict[str, Any]] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fallback: str = "benchmark_log"

    @property
    def command_audit(self) -> dict[str, Any]:
        return self.execution_manifest

    def to_dict(self, redactor: Callable[[Any], Any] | None = None) -> dict[str, Any]:
        payload = asdict(self)
        payload["command_audit"] = dict(self.execution_manifest)
        return (redactor or redact_sensitive)(payload)


def _persist_result(
    results_dir: Path,
    result: McProfilerCollectionResult,
    redactor: Callable[[Any], Any] | None,
) -> McProfilerCollectionResult:
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / "mcprofiler_collection.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_dict(redactor), ensure_ascii=False, sort_keys=True, default=str) + "\n")
    return result


def _result(
    request: McProfilerCollectionRequest,
    started_at: str,
    collection_id: str,
    status: CollectionStatus,
    reason: str,
    message: str,
    **kwargs: Any,
) -> McProfilerCollectionResult:
    execution_manifest = {
        "runner": "ssh",
        "tool": posixpath.basename(request.executable),
        "target_command_sha256": hashlib.sha256(request.target_command.encode("utf-8")).hexdigest(),
        "source_trial_id": request.source_trial_id,
        "source_sha256": request.source_sha256,
        "shape_id": request.shape_id,
        "working_directory_scope": "managed_remote_workspace",
        "credentials_forwarded_to_vendor": False,
    }
    execution_manifest.update(kwargs.pop("execution_manifest", {}))
    return McProfilerCollectionResult(
        status=status,
        reason_category=reason,
        message=message,
        case_name=request.case_name,
        collection_id=collection_id,
        source_trial_id=request.source_trial_id,
        source_sha256=request.source_sha256,
        shape_id=request.shape_id,
        started_at=started_at,
        finished_at=_utc_now(),
        metrics_requested=list(request.metrics),
        kernel_names=list(request.kernel_names),
        execution_manifest=execution_manifest,
        **kwargs,
    )


def _tool_check_command(executable: str) -> str:
    quoted = shlex.quote(executable)
    return f"test -x {quoted}" if "/" in executable else f"command -v {quoted}"


def _build_collection_command(
    request: McProfilerCollectionRequest,
    workspace: str,
    collection_root: str,
    port: int,
) -> str:
    client = [
        request.executable,
        "perf_exec",
        "--cmdline",
        request.target_command,
        "--casename",
        request.case_name,
        "--cwd",
        workspace,
        "--metrics",
        ",".join(request.metrics),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if request.kernel_names:
        client.extend(["--kernelname", ",".join(request.kernel_names)])
    server = " ".join(
        [shlex.quote(request.server_executable), "--host", "127.0.0.1", "--port", str(port)]
    )
    client_command = " ".join(shlex.quote(item) for item in client)
    script = (
        f"cd {shlex.quote(collection_root)}; server_pid=''; client_pid=''; "
        "cleanup() { if [ -n \"$client_pid\" ]; then kill -TERM \"$client_pid\" 2>/dev/null || true; "
        "wait \"$client_pid\" 2>/dev/null || true; fi; "
        f"if [ -n \"$server_pid\" ]; then kill -TERM \"$server_pid\" 2>/dev/null || true; "
        "wait \"$server_pid\" 2>/dev/null || true; fi; }; trap cleanup EXIT HUP INT TERM; "
        f"{server} > profiler_server.log 2>&1 & server_pid=$!; "
        f"sleep {request.startup_wait_seconds:g}; kill -0 \"$server_pid\" 2>/dev/null || "
        "{ echo 'managed mcProfiler server did not start' >&2; exit 70; }; "
        f"{client_command} & client_pid=$!; wait \"$client_pid\"; rc=$?; client_pid=''; exit \"$rc\""
    )
    return f"sh -c {shlex.quote(script)}"


def _artifact_manifest(case_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "relative_path": path.relative_to(case_dir).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "source": "mcprofiler_auto_collection",
        }
        for path in sorted(case_dir.rglob("*"))
        if path.is_file()
    ]


def _write_execution_logs(
    results_dir: Path,
    collection_id: str,
    stdout: str,
    stderr: str,
    redactor: Callable[[Any], Any] | None,
) -> dict[str, str]:
    log_dir = results_dir / "mcprofiler_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "stdout": log_dir / f"{collection_id}.stdout.log",
        "stderr": log_dir / f"{collection_id}.stderr.log",
    }
    sanitize = redactor or redact_sensitive
    paths["stdout"].write_text(str(sanitize(stdout or "")), encoding="utf-8")
    paths["stderr"].write_text(str(sanitize(stderr or "")), encoding="utf-8")
    return {name: path.relative_to(results_dir).as_posix() for name, path in paths.items()}


def _contains_secret(root: Path, secret_values: tuple[str, ...]) -> bool:
    needles = [value.encode("utf-8") for value in secret_values if value]
    if not needles:
        return False
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            if any(needle in content for needle in needles):
                return True
    return False


def _append_import_records(import_dir: Path, results_dir: Path) -> None:
    for filename in ("profiler_results.jsonl", "metric_observations.jsonl", "diagnoses.jsonl"):
        source = import_dir / filename
        if not source.is_file():
            continue
        with (results_dir / filename).open("a", encoding="utf-8") as output:
            for line in source.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    output.write(line + "\n")


def collect_remote_mcprofiler_case(
    runner: Any,
    results_dir: Path,
    request: McProfilerCollectionRequest,
    should_cancel: Callable[[], bool] | None = None,
    redactor: Callable[[Any], Any] | None = None,
    secret_values: tuple[str, ...] = (),
    import_output_dir: Path | None = None,
    append_imported_records: bool = False,
) -> McProfilerCollectionResult:
    started_at = _utc_now()
    collection_id = "mcprof-" + secrets.token_hex(12)

    def finish(status: CollectionStatus, reason: str, message: str, **kwargs: Any) -> McProfilerCollectionResult:
        safe_message = (redactor or redact_sensitive)(message)
        return _persist_result(
            results_dir,
            _result(request, started_at, collection_id, status, reason, str(safe_message), **kwargs),
            redactor,
        )

    if should_cancel and should_cancel():
        return finish("cancelled", "cancelled", "collection cancelled before starting")
    if not all(hasattr(runner, name) for name in ("run", "run_cancellable", "find_files", "file_sha256", "download")):
        return finish("unsupported", "runner_unsupported", "automatic mcProfiler collection requires an SSH runner with managed artifact access")
    workspace = getattr(getattr(runner, "info", None), "remote_workspace", None)
    if not workspace:
        return finish("unsupported", "runner_unsupported", "automatic mcProfiler collection requires a managed remote workspace")
    guard = validate(request.target_command, workspace, workspace, getattr(runner, "denied_commands", None))
    if not guard.allowed:
        return finish("failed", "guard_denied", guard.reason)
    if any(value and value in request.target_command for value in secret_values):
        return finish("failed", "secret_in_command", "target command contains a configured secret value")

    for label, executable in (("client", request.executable), ("server", request.server_executable)):
        try:
            check = runner.run(f"mcprofiler_{label}_check", _tool_check_command(executable))
        except Exception as exc:
            return finish("failed", "runner_exception", f"official mcProfiler {label} tool check failed: {type(exc).__name__}: {exc}")
        if check.guard_denied:
            return finish("failed", "guard_denied", check.error_message or check.stderr or f"{label} tool check denied")
        if check.returncode != 0:
            return finish("unsupported", "tool_unavailable", f"official mcProfiler {label} executable is unavailable in the remote environment")

    collection_root = f".kernel_opt_agent/mcprofiler/collections/{collection_id}"
    prepare_command = f"test ! -e {shlex.quote(collection_root)} && mkdir -p {shlex.quote(collection_root)}"
    try:
        prepared = runner.run("mcprofiler_prepare", prepare_command)
    except Exception as exc:
        return finish("failed", "runner_exception", f"managed collection directory preparation failed: {type(exc).__name__}: {exc}")
    if prepared.guard_denied:
        return finish("failed", "guard_denied", prepared.error_message or prepared.stderr or "collection directory preparation denied")
    if prepared.returncode != 0:
        return finish("failed", "collection_workspace_failed", "exclusive managed collection directory could not be created")

    port = request.service_port or (49152 + secrets.randbelow(12000))
    command = _build_collection_command(request, workspace, collection_root, port)
    try:
        execution = runner.run_cancellable(
            "mcprofiler_collection", command, should_cancel, timeout_seconds=request.timeout_seconds
        )
    except Exception as exc:
        return finish("failed", "runner_exception", f"official mcProfiler execution failed: {type(exc).__name__}: {exc}")
    log_paths = _write_execution_logs(results_dir, collection_id, execution.stdout, execution.stderr, redactor)
    execution_manifest = {
        "controlled_remote_directory": collection_root,
        "returncode": execution.returncode,
        "duration_seconds": execution.duration,
        "timed_out": execution.timeout,
        "guard_denied": execution.guard_denied,
    }
    if execution.guard_denied:
        return finish("failed", "guard_denied", execution.error_message or execution.stderr or "collection command denied", logs=log_paths, execution_manifest=execution_manifest)
    if execution.returncode == 130 or (should_cancel and should_cancel()):
        return finish("cancelled", "cancelled", "official mcProfiler collection was cancelled", logs=log_paths, execution_manifest=execution_manifest)
    if execution.timeout:
        return finish("failed", "timeout", execution.error_message or "official mcProfiler collection timed out", logs=log_paths, execution_manifest=execution_manifest)
    if execution.returncode != 0:
        return finish("failed", "collection_failed", execution.error_message or execution.stderr[-500:] or "official mcProfiler command failed", logs=log_paths, execution_manifest=execution_manifest)

    try:
        reports = runner.find_files("report_dumped_result.json", relative_root=collection_root)
        report_hashes = {path: runner.file_sha256(path) for path in reports}
    except Exception as exc:
        return finish("failed", "discovery_failed", f"could not inspect collected mcProfiler reports: {type(exc).__name__}: {exc}", logs=log_paths, execution_manifest=execution_manifest)
    if len(reports) != 1:
        reason = "report_not_found" if not reports else "ambiguous_reports"
        return finish("failed", reason, f"expected one report in the exclusive collection directory, found {len(reports)}", logs=log_paths, execution_manifest=execution_manifest)
    report_path = reports[0]
    if not (report_path == collection_root or report_path.startswith(collection_root + "/")):
        return finish("failed", "discovery_failed", "mcProfiler report resolved outside the exclusive collection directory", logs=log_paths, execution_manifest=execution_manifest)
    report_parent = posixpath.dirname(report_path)
    case_root = posixpath.dirname(report_parent) if posixpath.basename(report_parent) == "case_report" else report_parent
    if not (case_root == collection_root or case_root.startswith(collection_root + "/")):
        return finish("failed", "discovery_failed", "mcProfiler Case root resolved outside the exclusive collection directory", logs=log_paths, execution_manifest=execution_manifest)

    local_case = results_dir / "mcprofiler_cases" / collection_id
    local_case.resolve().relative_to(results_dir.resolve())
    if local_case.exists():
        return finish("failed", "local_case_exists", "exclusive local collection directory already exists; no files were overwritten", logs=log_paths, execution_manifest=execution_manifest)
    try:
        runner.download(
            case_root,
            local_case,
            max_files=request.max_artifact_files,
            max_bytes=request.max_artifact_bytes,
        )
    except Exception as exc:
        if local_case.exists():
            local_case.resolve().relative_to((results_dir / "mcprofiler_cases").resolve())
            shutil.rmtree(local_case)
        return finish("failed", "artifact_download_failed", f"mcProfiler Case download failed: {type(exc).__name__}: {exc}", logs=log_paths, execution_manifest=execution_manifest)
    if _contains_secret(local_case, secret_values):
        shutil.rmtree(local_case)
        return finish("failed", "secret_detected", "downloaded mcProfiler artifacts contained a configured secret and were removed", logs=log_paths, execution_manifest=execution_manifest)

    try:
        from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
        from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler

        import_dir = import_output_dir or (
            results_dir / "mcprofiler_imports" / collection_id
            if append_imported_records
            else results_dir
        )
        imported = import_report(local_case, import_dir, source_trial_id=request.source_trial_id)
        if append_imported_records:
            _append_import_records(import_dir, results_dir)
        profiler_result = MxmacaProfiler().collect({"mcprofiler_case_path": str(local_case)})
    except Exception as exc:
        return finish("failed", "parse_failed", f"collected mcProfiler Case could not be parsed: {type(exc).__name__}: {exc}", logs=log_paths, execution_manifest=execution_manifest)
    available = dict(profiler_result.available_metrics)
    warnings = [] if any(available.values()) else ["mcProfiler Case was collected, but no normalized metrics were available"]
    execution_manifest["report_sha256"] = report_hashes[report_path]
    execution_manifest["import_artifact_id"] = imported.get("artifact_id")
    return finish(
        "collected",
        "none",
        "official mcProfiler completed and the resulting Case was imported",
        case_dir=f"mcprofiler_cases/{collection_id}",
        artifact_manifest=_artifact_manifest(local_case),
        available_metrics=available,
        metric_observations=[item.to_dict() for item in profiler_result.observations],
        logs=log_paths,
        warnings=warnings,
        execution_manifest=execution_manifest,
    )
