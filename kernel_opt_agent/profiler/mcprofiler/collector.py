from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
import secrets
import shlex
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from kernel_opt_agent.profiler.base import UNVERIFIED_ATTRIBUTION_WARNING, redact_sensitive
from kernel_opt_agent.runner.command_guard import validate

from .tool_discovery import discover_mcprofiler, persist_capability, validate_tool_path


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
    per_kernel: bool = False
    profile_from_start: bool = True
    server_mode: Literal["embedded", "managed_external"] = "embedded"
    discovery_hint: dict[str, Any] | None = None

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
            validate_tool_path(executable, "mcProfiler" if label == "executable" else "profiler_server")
        if self.service_port is not None and not 1024 <= self.service_port <= 65535:
            raise ValueError("mcProfiler service_port must be between 1024 and 65535")
        if self.startup_wait_seconds <= 0 or self.startup_wait_seconds > 30:
            raise ValueError("mcProfiler startup_wait_seconds must be in (0, 30]")
        if self.server_mode not in {"embedded", "managed_external"}:
            raise ValueError("mcProfiler server_mode must be embedded or managed_external")
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
    partial_artifact_dir: str | None = None
    primary_report: str | None = None
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
        # Older MXMACA runtimes do not auto-enable the profiling RPC hook.
        "sh -c " + shlex.quote("export MCTX_TARGET_INIT=true; " + request.target_command),
        "--casename",
        request.case_name,
        "--cwd",
        workspace,
        "--output",
        posixpath.join(workspace, collection_root),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if request.metrics:
        client.extend(["--metrics", *request.metrics])
    if request.kernel_names:
        client.extend(["--kernelnames", *request.kernel_names])
    if request.per_kernel:
        client.append("--per-kernel")
    if not request.profile_from_start:
        client.append("--custom")
    server = " ".join(
        [shlex.quote(request.server_executable), "--host", "127.0.0.1", "--port", str(port)]
    )
    client_command = " ".join(shlex.quote(item) for item in client)
    absolute_root = posixpath.join(workspace, collection_root)
    prepare_environment = (
        f"cd {shlex.quote(collection_root)}; "
        "mkdir -p .home .tmp; "
        f"export HOME={shlex.quote(posixpath.join(absolute_root, '.home'))}; "
        f"export TMPDIR={shlex.quote(posixpath.join(absolute_root, '.tmp'))}; "
    )
    start_server = ""
    if request.server_mode == "managed_external":
        start_server = (
            f"{server} > profiler_server.log 2>&1 & server_pid=$!; "
            f"sleep {request.startup_wait_seconds:g}; kill -0 \"$server_pid\" 2>/dev/null || "
            "{ echo 'managed mcProfiler server did not start' >&2; exit 70; }; "
        )
    script = (
        prepare_environment + "server_pid=''; client_pid=''; "
        "cleanup() { if [ -n \"$client_pid\" ]; then kill -TERM \"$client_pid\" 2>/dev/null || true; "
        "wait \"$client_pid\" 2>/dev/null || true; fi; "
        f"if [ -n \"$server_pid\" ]; then kill -TERM \"$server_pid\" 2>/dev/null || true; "
        "wait \"$server_pid\" 2>/dev/null || true; fi; }; trap cleanup EXIT HUP INT TERM; "
        + start_server +
        f"{client_command} & client_pid=$!; wait \"$client_pid\"; rc=$?; client_pid=''; exit \"$rc\""
    )
    return f"sh -c {shlex.quote(script)}"


def _artifact_manifest(case_dir: Path, source: str = "mcprofiler_auto_collection") -> list[dict[str, Any]]:
    return [
        {
            "relative_path": path.relative_to(case_dir).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "source": source,
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


def _preserve_partial_artifacts(
    runner: Any,
    results_dir: Path,
    collection_root: str,
    collection_id: str,
    request: McProfilerCollectionRequest,
    secret_values: tuple[str, ...],
) -> dict[str, Any]:
    partial_root = results_dir / "mcprofiler_failed"
    destination = partial_root / collection_id
    destination.resolve().relative_to(results_dir.resolve())
    if destination.exists():
        return {"warnings": ["partial artifact directory exists; no files were overwritten"]}
    try:
        runner.download(collection_root, destination, max_files=request.max_artifact_files,
                        max_bytes=request.max_artifact_bytes)
        if _contains_secret(destination, secret_values):
            raise ValueError("configured secret detected in partial artifacts")
        return {
            "partial_artifact_dir": destination.relative_to(results_dir).as_posix(),
            "artifact_manifest": _artifact_manifest(destination, "mcprofiler_failed_collection"),
            "warnings": ["partial artifacts are debugging evidence, not a valid profiler Case"],
        }
    except Exception as exc:
        if destination.exists():
            destination.resolve().relative_to(partial_root.resolve())
            shutil.rmtree(destination)
        return {"warnings": ["partial artifact preservation failed: " + type(exc).__name__]}


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
    discovery_payload = None

    def finish(status: CollectionStatus, reason: str, message: str, **kwargs: Any) -> McProfilerCollectionResult:
        safe_message = (redactor or redact_sensitive)(message)
        if discovery_payload is not None:
            kwargs.setdefault("execution_manifest", {})["tool_discovery"] = discovery_payload
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

    capability = discover_mcprofiler(
        runner, request.executable, request.server_executable, results_dir=results_dir,
        timeout_seconds=min(20, request.timeout_seconds), should_cancel=should_cancel, redactor=redactor,
    )
    hint = request.discovery_hint or {}
    if (capability.available and hint.get("available") is True
            and hint.get("executable") == capability.executable
            and hint.get("server_executable") == capability.server_executable
            and hint.get("version") == capability.version
            and hint.get("discovery_source") in {"explicit", "path", "allowlist"}):
        capability.discovery_source = hint["discovery_source"]
    discovery_payload = capability.model_dump(mode="json")
    persist_capability(results_dir, capability, redactor)
    if not capability.available:
        status = "cancelled" if capability.status == "cancelled" else "unsupported" if capability.status == "unavailable" else "failed"
        reason = "tool_unavailable" if capability.status == "unavailable" else capability.reason_category
        return finish(status, reason, "mcProfiler discovery: " + capability.reason_category,
                      logs=capability.logs, warnings=capability.warnings)
    request = replace(request, executable=capability.executable, server_executable=capability.server_executable,
                      server_mode="managed_external" if request.server_mode == "managed_external" else capability.server_mode)
    tool_version = capability.version

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
        "server_mode": request.server_mode,
        "profile_from_start_requested": request.profile_from_start,
        "runtime_profiling_hook": "MCTX_TARGET_INIT=true",
    }

    def failed_execution(status: CollectionStatus, reason: str, message: str) -> McProfilerCollectionResult:
        partial = _preserve_partial_artifacts(
            runner, results_dir, collection_root, collection_id, request, secret_values,
        )
        return finish(status, reason, message, logs=log_paths,
                      execution_manifest=execution_manifest, **partial)

    if execution.guard_denied:
        return finish("failed", "guard_denied", execution.error_message or execution.stderr or "collection command denied", logs=log_paths, execution_manifest=execution_manifest)
    if "stop could not be verified" in (execution.error_message or ""):
        return finish("failed", "cleanup_unverified", execution.error_message, logs=log_paths, execution_manifest=execution_manifest)
    if execution.returncode == 130 or (should_cancel and should_cancel()):
        return failed_execution("cancelled", "cancelled", "official mcProfiler collection was cancelled")
    if execution.timeout:
        return failed_execution("failed", "timeout", execution.error_message or "official mcProfiler collection timed out")
    if execution.returncode != 0:
        return failed_execution("failed", "collection_failed", execution.error_message or execution.stderr[-500:] or "official mcProfiler command failed")

    try:
        reports = runner.find_files("report_dumped_result.json", relative_root=collection_root)
        report_hashes = {path: runner.file_sha256(path) for path in reports}
    except Exception as exc:
        return failed_execution("failed", "discovery_failed", f"could not inspect collected mcProfiler reports: {type(exc).__name__}: {exc}")
    if len(reports) != 1:
        reason = "report_not_found" if not reports else "ambiguous_reports"
        return failed_execution("failed", reason, f"expected one report in the exclusive collection directory, found {len(reports)}")
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
        local_case.resolve().relative_to((results_dir / "mcprofiler_cases").resolve())
        shutil.rmtree(local_case)
        return finish("failed", "secret_detected", "downloaded mcProfiler artifacts contained a configured secret and were removed", logs=log_paths, execution_manifest=execution_manifest)

    downloaded_report = local_case.joinpath(*posixpath.relpath(report_path, case_root).split("/"))
    try:
        downloaded_report.resolve().relative_to(local_case.resolve())
        if not downloaded_report.is_file() or _sha256(downloaded_report) != report_hashes[report_path]:
            return finish("failed", "artifact_hash_mismatch", "downloaded report differs from the report inspected on the execution runner", logs=log_paths, execution_manifest=execution_manifest)
    except (OSError, ValueError):
        return finish("failed", "artifact_hash_mismatch", "downloaded report could not be verified", logs=log_paths, execution_manifest=execution_manifest)

    if request.per_kernel:
        candidates = [
            path for path in local_case.rglob("*_dumped_result.json")
            if re.fullmatch(r"\d+_.+_dumped_result\.json", path.name)
            and not re.fullmatch(r"\d+_period\d+_dumped_result\.json", path.name)
            and (not request.kernel_names or any(
                re.fullmatch(r"\d+_" + re.escape(name) + r"_dumped_result\.json", path.name)
                for name in request.kernel_names
            ))
        ]
        if len(candidates) != 1:
            return finish(
                "failed", "target_report_not_found", "expected exactly one unambiguous per-kernel report; aggregate reports are not a target fallback",
                case_dir=f"mcprofiler_cases/{collection_id}", artifact_manifest=_artifact_manifest(local_case),
                logs=log_paths, execution_manifest=execution_manifest,
            )
        downloaded_report = candidates[0]
        selected_remote = posixpath.join(case_root, downloaded_report.relative_to(local_case).as_posix())
        try:
            selected_hash = runner.file_sha256(selected_remote)
            if selected_hash != _sha256(downloaded_report):
                raise ValueError("per-kernel report hash changed")
        except Exception:
            return finish("failed", "artifact_hash_mismatch", "per-kernel report could not be verified",
                          logs=log_paths, execution_manifest=execution_manifest)
        execution_manifest["report_scope"] = "named_kernel"
    else:
        selected_hash = report_hashes[report_path]
        execution_manifest["report_scope"] = "aggregate"

    try:
        from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
        from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler

        visible = {key: value for key, value in getattr(runner, "execution_env", {}).items()
                   if key in {"MACA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"}}
        device_id = ("device_sha256:" + hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()) if visible else None
        context = {
            "tool_version": tool_version, "device_id": device_id,
            "requested_kernel": request.kernel_names[0] if len(request.kernel_names) == 1 else None,
            "source_sha256": request.source_sha256, "report_sha256": selected_hash,
            "collection_window": {"started_at": started_at, "finished_at": _utc_now(),
                                  "kind": "collector_wall_clock", "target_only_window_verified": False},
        }
        # An agent sidecar never rewrites native evidence or promotes attribution.
        (local_case / "agent_collection_context.json").write_text(json.dumps(context, sort_keys=True), encoding="utf-8")
        execution_manifest["collection_context"] = context
        profiler_result = MxmacaProfiler().collect({"mcprofiler_case_path": str(downloaded_report)})
        if not any(
            item.available and isinstance(item.value, (int, float))
            and not isinstance(item.value, bool) and math.isfinite(item.value)
            for item in profiler_result.observations
        ):
            return finish(
                "failed", "report_invalid", "mcProfiler report contains no usable numeric counters",
                case_dir=f"mcprofiler_cases/{collection_id}", artifact_manifest=_artifact_manifest(local_case),
                logs=log_paths, execution_manifest=execution_manifest,
            )
        import_dir = import_output_dir or (
            results_dir / "mcprofiler_imports" / collection_id
            if append_imported_records
            else results_dir
        )
        imported = import_report(downloaded_report, import_dir, source_trial_id=request.source_trial_id)
        if append_imported_records:
            _append_import_records(import_dir, results_dir)
    except Exception as exc:
        return finish("failed", "parse_failed", f"collected mcProfiler Case could not be parsed: {type(exc).__name__}: {exc}", logs=log_paths, execution_manifest=execution_manifest)
    available = dict(profiler_result.available_metrics)
    warnings = [UNVERIFIED_ATTRIBUTION_WARNING]
    if not any(available.values()):
        warnings.append("mcProfiler Case was collected, but no normalized metrics were available")
    execution_manifest["target_attribution"] = {"status": "unverified", "confidence": "low"}
    execution_manifest["report_sha256"] = selected_hash
    execution_manifest["import_artifact_id"] = imported.get("artifact_id")
    return finish(
        "collected",
        "none",
        "official mcProfiler completed and the resulting Case was imported",
        case_dir=f"mcprofiler_cases/{collection_id}",
        primary_report=downloaded_report.relative_to(local_case).as_posix(),
        artifact_manifest=_artifact_manifest(local_case),
        available_metrics=available,
        metric_observations=[item.to_dict() for item in profiler_result.observations],
        logs=log_paths,
        warnings=warnings,
        execution_manifest=execution_manifest,
    )
