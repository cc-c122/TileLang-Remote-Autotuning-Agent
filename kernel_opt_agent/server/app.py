from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from kernel_opt_agent.hardware.hardware_info import CANONICAL_FIELDS, HardwareInfo
from kernel_opt_agent.hardware.profile_loader import HardwareProfileLoader, normalize_profile_name
from kernel_opt_agent.main import WORKSPACE_ROOT
from kernel_opt_agent.source_optimizer import read_source_result

from .job_worker import JobWorker
from .models import HardwareResolveRequest, SettingsPayload, TaskCreateRequest
from .profiler_status import profiler_status
from .sample_uploads import SampleUploadError, SampleUploadStore, parse_sample_upload_request
from .settings_store import SettingsStore
from .task_manager import TaskManager


SERVER_WORKSPACE = WORKSPACE_ROOT / "server"
TASKS_ROOT = WORKSPACE_ROOT / "tasks"
UPLOADS_ROOT = SERVER_WORKSPACE / "uploads"
SETTINGS_PATH = WORKSPACE_ROOT / "server_settings.yaml"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _read_yaml(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    from .profiler_status import read_jsonl

    return read_jsonl(path)


def _summary_with_correctness(results_dir: Path, summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    experiments = _read_jsonl(results_dir / "experiments.jsonl")
    by_identity = {
        (str(item.get("run_id")), str(item.get("iteration")), str(item.get("candidate_id")), str(item.get("config_hash"))): item.get("correctness")
        for item in experiments
    }
    return [
        {
            **row,
            "correctness": by_identity.get(
                (str(row.get("run_id")), str(row.get("iteration")), str(row.get("candidate_id")), str(row.get("config_hash")))
            ),
        }
        for row in summary
    ]


def _numeric(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_summary(summary_rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float | None]:
    ok = [row for row in summary_rows if row.get("status") == "benchmark_ok" and _numeric(row.get("objective_value")) is not None]
    if not ok:
        return None, None
    best = min(ok, key=lambda row: _numeric(row.get("objective_value")) or float("inf"))
    baseline = summary_rows[0] if summary_rows else None
    baseline_value = _numeric((baseline or {}).get("objective_value"))
    best_value = _numeric(best.get("objective_value"))
    improvement = None
    if baseline_value and best_value is not None:
        improvement = (baseline_value - best_value) / baseline_value * 100.0
    return best, improvement


def _task_payload(task: Any) -> dict[str, Any]:
    payload = task.model_dump()
    summary = _read_csv(Path(task.results_dir) / "summary.csv")
    best_row, improvement = _best_summary(summary)
    if task.execution_mode == "baseline_only":
        improvement = None
    baseline = summary[0] if summary else {}
    iterations = [_numeric(row.get("iteration")) for row in summary]
    latest_event = task.events[-1] if task.events else {}
    source_optimization = read_source_result(Path(task.results_dir))
    accepted = next(
        (item for item in source_optimization.get("trials", []) if item.get("trial_id") == source_optimization.get("accepted_trial_id")),
        None,
    )
    accepted_latency = None
    if accepted is not None:
        improvement = _numeric(accepted.get("improvement_percent"))
        accepted_latency = _numeric(accepted.get("candidate_median_latency_ms"))
    payload.update(
        {
            "current_iteration": int(max([item for item in iterations if item is not None], default=0)),
            "total_trials": len(summary),
            "best_latency": accepted_latency if accepted is not None else _numeric((best_row or {}).get("latency")),
            "baseline_latency": _numeric(baseline.get("latency")),
            "improvement_percent": improvement,
            "current_stage": latest_event.get("type") or task.status,
            "latest_message": latest_event.get("message") or task.status,
        }
    )
    return payload


def _result_payload(results_dir: Path, execution_mode: str | None = None) -> dict[str, Any]:
    summary = _summary_with_correctness(results_dir, _read_csv(results_dir / "summary.csv"))
    best_row, improvement = _best_summary(summary)
    if execution_mode == "baseline_only":
        improvement = None
    source_optimization = read_source_result(results_dir)
    accepted = next(
        (item for item in source_optimization.get("trials", []) if item.get("trial_id") == source_optimization.get("accepted_trial_id")),
        None,
    )
    if accepted is not None:
        improvement = _numeric(accepted.get("improvement_percent"))
        accepted_hash = accepted.get("source_after_sha256")
        best_row = next((row for row in summary if row.get("config_hash") == accepted_hash), best_row)
    files = {}
    for name in [
        "experiments.jsonl",
        "summary.csv",
        "profiler_results.jsonl",
        "metric_observations.jsonl",
        "diagnosis.jsonl",
        "diagnoses.jsonl",
        "patch_trials.jsonl",
        "best_kernel.py",
        "best_config.yaml",
        "report.md",
        "source_optimization.json",
    ]:
        path = results_dir / name
        files[name] = {"exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
    best_kernel = _read_text(results_dir / "best_kernel.py")
    best_config = _read_yaml(results_dir / "best_config.yaml")
    report = _read_text(results_dir / "report.md")
    evidence_summary = _read_jsonl(results_dir / "metric_observations.jsonl")
    diagnoses = _read_jsonl(results_dir / "diagnoses.jsonl")
    profiler_rows = _read_jsonl(results_dir / "profiler_results.jsonl")
    status = profiler_status(profiler_rows, evidence_summary)
    payload = {
        "results_dir": str(results_dir),
        "best_kernel": best_kernel,
        "best_config": best_config,
        "report_markdown": report,
        "summary_table": summary,
        "improvement_percent": improvement,
        "failed_cases": _read_jsonl(results_dir / "failed_cases.jsonl"),
        "generated_files": files,
        "evidence_summary": evidence_summary,
        "diagnoses": diagnoses,
        "profiler_status": status,
        "profiler_available": status == "profiler_metrics_available",
        "source_optimization": source_optimization,
    }
    payload.update({"files": files, "summary": summary, "best_row": best_row, "report": report})
    return payload


def _profile_field_values(profile: dict[str, Any]) -> dict[str, Any]:
    values = {name: profile.get(name) for name in CANONICAL_FIELDS if name in profile}
    mma = profile.get("mma")
    if isinstance(mma, dict) and "supported_dtypes" in mma:
        values.setdefault("supported_dtypes", mma["supported_dtypes"])
    return values


def resolve_hardware(request: HardwareResolveRequest) -> dict[str, Any]:
    loader = HardwareProfileLoader()
    info = HardwareInfo.unknown()
    profile_name, profile = loader.load(request.gpu_model or "unknown_gpu")
    info.profile_used = profile_name
    for field_name, value in _profile_field_values(profile).items():
        if value is not None:
            info.set_field(field_name, value, "builtin_profile", "medium", f"from built-in {profile_name} profile")
        else:
            info.set_field(field_name, None, "unknown", "unknown", f"not specified by built-in {profile_name} profile")
    if request.gpu_model:
        info.set_field("target_name", request.gpu_model, "user_override", "high", "from web request target.gpu_model")
    if request.backend and request.backend != "unknown":
        info.set_field("backend", request.backend, "user_override", "high", "from web request target.backend")
    for name, value in request.user_overrides.items():
        info.set_field(name, value, "user_override", "high", f"from web request user_overrides.{name}")
    if request.allow_llm_lookup:
        info.warnings.append("LLM hardware lookup is reserved for a later phase and was not used")
    if request.allow_web_lookup:
        info.warnings.append("Web hardware lookup is disabled by default and was not used")
    return info.to_dict()


def _redact_connection_error(message: str, settings: SettingsPayload) -> str:
    redacted = message
    for env_name in (settings.ssh.password_env, settings.llm.api_key_env):
        if env_name:
            value = os.environ.get(env_name)
            if value:
                redacted = redacted.replace(value, "<redacted:secret>")
    if settings.ssh.key_path:
        redacted = redacted.replace(settings.ssh.key_path, "<redacted:key_path>")
        redacted = redacted.replace(os.path.expanduser(settings.ssh.key_path), "<redacted:key_path>")
    return redacted


manager = TaskManager(TASKS_ROOT)
upload_store = SampleUploadStore(UPLOADS_ROOT)
worker = JobWorker(manager, SETTINGS_PATH, upload_store)
settings_store = SettingsStore(SETTINGS_PATH)
app = FastAPI(title="TileLang Remote Autotuning Agent API")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "tilelang-agent",
        "mode": "local-runner",
        "capabilities": {"source_optimization": True},
    }


@app.post("/api/settings")
def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    return {"ok": True, "settings": settings_store.save(payload)}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"ok": True, "settings": settings_store.load()}


@app.post("/api/settings/test-connection")
def test_connection() -> dict[str, Any]:
    settings = settings_store.load_raw()
    ssh = settings.ssh
    try:
        if not ssh.host.strip() or not ssh.username.strip():
            raise ValueError("SSH host and username are required")
        if ssh.auth_type == "password":
            if not ssh.password_env:
                raise ValueError("password_env is required for SSH password auth")
            if not os.environ.get(ssh.password_env):
                raise ValueError(f"SSH password env var is not set: {ssh.password_env}")
        if ssh.auth_type == "key" and not ssh.key_path:
            raise ValueError("key_path is required for SSH key auth")
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SSH test requires optional dependency 'paramiko'") from exc
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            if ssh.auth_type == "password":
                client.connect(
                    hostname=ssh.host,
                    port=ssh.port,
                    username=ssh.username,
                    password=os.environ[ssh.password_env or ""],
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )
            else:
                client.connect(
                    hostname=ssh.host,
                    port=ssh.port,
                    username=ssh.username,
                    key_filename=os.path.expanduser(ssh.key_path or ""),
                    timeout=10,
                    look_for_keys=True,
                    allow_agent=True,
                )
        finally:
            client.close()
        return {"ok": True, "success": True, "failure": False, "error_message": None, "settings": settings_store.load()}
    except Exception as exc:
        return {"ok": True, "success": False, "failure": True, "error_message": _redact_connection_error(str(exc), settings), "settings": settings_store.load()}


@app.post("/api/hardware/resolve")
def hardware_resolve(payload: HardwareResolveRequest) -> dict[str, Any]:
    return {"ok": True, "hardware": resolve_hardware(payload)}


@app.post("/api/samples/upload")
async def upload_sample(request: Request) -> dict[str, Any]:
    files = []
    try:
        files, entry_file = await parse_sample_upload_request(request)
        manifest = await upload_store.create(files, entry_file)
    except SampleUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        for upload in files:
            await upload.close()
    return {
        "ok": True,
        "upload_id": manifest["upload_id"],
        "entry_file": manifest["entry_file"],
        "files": [
            {"path": item["path"], "size_bytes": item["size_bytes"]}
            for item in manifest["files"]
        ],
    }


@app.post("/api/tasks")
def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
    if payload.sample.source_type == "upload":
        try:
            upload_store.validate_reference(payload.sample.upload_id or "", payload.sample.entry_file)
        except SampleUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    task = manager.create(payload)
    worker.submit(task.task_id, payload)
    return {"ok": True, "task_id": task.task_id, "task": _task_payload(task)}


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    tasks = []
    for task in manager.list_recent():
        payload = _task_payload(task)
        tasks.append(
            {
                "task_id": payload["task_id"],
                "project_name": payload["project_name"],
                "status": payload["status"],
                "created_at": payload["created_at"],
                "started_at": payload["started_at"],
                "finished_at": payload["finished_at"],
                "improvement_percent": payload["improvement_percent"],
                "latest_message": payload["latest_message"],
            }
        )
    return {"ok": True, "tasks": tasks}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "task": _task_payload(manager.get(task_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.get("/api/tasks/{task_id}/events")
def get_task_events(task_id: str) -> dict[str, Any]:
    try:
        task = manager.get(task_id)
        return {"ok": True, "task_id": task_id, "status": task.status, "events": task.events}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.get("/api/tasks/{task_id}/results")
def get_task_results(task_id: str) -> dict[str, Any]:
    try:
        task = manager.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    return {
        "ok": True,
        "task": _task_payload(task),
        "results": _result_payload(Path(task.results_dir), task.execution_mode),
    }


def _download(task_id: str, filename: str) -> FileResponse:
    try:
        task = manager.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    path = Path(task.results_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    if filename == "best_kernel.py" and task.execution_mode == "source_optimization":
        source_result = read_source_result(Path(task.results_dir))
        if source_result.get("baseline_verified") is not True:
            raise HTTPException(status_code=409, detail="source optimization baseline was not verified")
        expected_hash = source_result.get("best_source_sha256")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if not expected_hash or actual_hash != expected_hash:
            raise HTTPException(status_code=409, detail="best kernel hash does not match the verified source result")
    return FileResponse(path, filename=filename)


@app.get("/api/tasks/{task_id}/download/best_kernel")
def download_best_kernel(task_id: str) -> FileResponse:
    return _download(task_id, "best_kernel.py")


@app.get("/api/tasks/{task_id}/download/report")
def download_report(task_id: str) -> FileResponse:
    return _download(task_id, "report.md")


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, Any]:
    try:
        task = manager.request_cancel(task_id)
        return {"ok": True, "task": _task_payload(task)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.api_route(
    "/api/{unmatched_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
def unknown_api_route(unmatched_path: str) -> None:
    raise HTTPException(status_code=404, detail="API route not found")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("kernel_opt_agent.server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
