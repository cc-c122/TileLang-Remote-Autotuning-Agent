from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from kernel_opt_agent.hardware.hardware_info import CANONICAL_FIELDS, HardwareInfo
from kernel_opt_agent.hardware.profile_loader import HardwareProfileLoader, normalize_profile_name
from kernel_opt_agent.main import WORKSPACE_ROOT

from .job_worker import JobWorker
from .models import HardwareResolveRequest, SettingsPayload, TaskCreateRequest
from .settings_store import SettingsStore
from .task_manager import TaskManager


SERVER_WORKSPACE = WORKSPACE_ROOT / "server"
TASKS_ROOT = WORKSPACE_ROOT / "tasks"
SETTINGS_PATH = WORKSPACE_ROOT / "server_settings.yaml"


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
    if not path.exists() or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"parse_error": True, "raw": line})
    return rows


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


def _result_payload(results_dir: Path) -> dict[str, Any]:
    summary = _read_csv(results_dir / "summary.csv")
    best_row, improvement = _best_summary(summary)
    files = {}
    for name in [
        "experiments.jsonl",
        "summary.csv",
        "profiler_results.jsonl",
        "diagnosis.jsonl",
        "patch_trials.jsonl",
        "best_kernel.py",
        "best_config.yaml",
        "report.md",
    ]:
        path = results_dir / name
        files[name] = {"exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
    best_kernel = _read_text(results_dir / "best_kernel.py")
    best_config = _read_yaml(results_dir / "best_config.yaml")
    report = _read_text(results_dir / "report.md")
    payload = {
        "results_dir": str(results_dir),
        "best_kernel": best_kernel,
        "best_config": best_config,
        "report_markdown": report,
        "summary_table": summary,
        "improvement_percent": improvement,
        "failed_cases": _read_jsonl(results_dir / "failed_cases.jsonl"),
        "generated_files": files,
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


manager = TaskManager(TASKS_ROOT)
worker = JobWorker(manager)
settings_store = SettingsStore(SETTINGS_PATH)
app = FastAPI(title="TileLang Remote Autotuning Agent API")


@app.post("/api/settings")
def save_settings(payload: SettingsPayload) -> dict[str, Any]:
    return {"ok": True, "settings": settings_store.save(payload)}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"ok": True, "settings": settings_store.load()}


@app.post("/api/hardware/resolve")
def hardware_resolve(payload: HardwareResolveRequest) -> dict[str, Any]:
    return {"ok": True, "hardware": resolve_hardware(payload)}


@app.post("/api/tasks")
def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
    task = manager.create(payload)
    worker.submit(task.task_id, payload)
    return {"ok": True, "task_id": task.task_id, "task": task.model_dump()}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "task": manager.get(task_id).model_dump()}
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
    return {"ok": True, "task": task.model_dump(), "results": _result_payload(Path(task.results_dir))}


def _download(task_id: str, filename: str) -> FileResponse:
    try:
        task = manager.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    path = Path(task.results_dir) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
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
        return {"ok": True, "task": task.model_dump()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("kernel_opt_agent.server.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
