from __future__ import annotations

import json
import threading
from pathlib import Path

import kernel_opt_agent.main as agent_main

from .models import TaskCreateRequest
from .run_request_builder import build_effective_config
from .task_manager import TaskManager


WORKER_LOCK = threading.Lock()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"parse_error": True})
    return rows


def _profiler_status(results_dir: Path) -> str:
    evidence_rows = _read_jsonl(results_dir / "metric_observations.jsonl")
    if evidence_rows:
        return "profiler_metrics_available"
    profiler_rows = _read_jsonl(results_dir / "profiler_results.jsonl")
    enabled_rows = [item for item in profiler_rows if item.get("profiler", {}).get("enabled")]
    if not enabled_rows:
        return "disabled"
    if any(item.get("profiler", {}).get("error") for item in enabled_rows):
        return "collection_failed"
    benchmark_fields = {"latency_ms", "tflops", "estimated_hbm_bandwidth"}
    for item in enabled_rows:
        result = item.get("profiler", {}).get("result") or {}
        available = result.get("available_metrics") or {}
        if any(available.get(name) for name in benchmark_fields):
            return "benchmark_only"
    return "collection_failed"


class JobWorker:
    def __init__(self, manager: TaskManager, settings_path: Path | None = None):
        self.manager = manager
        self.settings_path = settings_path

    def submit(self, task_id: str, request: TaskCreateRequest) -> None:
        thread = threading.Thread(target=self._run_task, args=(task_id, request), daemon=True)
        thread.start()

    def _run_task(self, task_id: str, request: TaskCreateRequest) -> None:
        task = self.manager.get(task_id)
        workspace = Path(task.workspace)
        results_dir = Path(task.results_dir)
        generated_dir = workspace / "generated"
        patches_dir = workspace / "patches"
        self.manager.add_event(task_id, "task_queued", "waiting for runner slot")
        with WORKER_LOCK:
            if self.manager.is_cancel_requested(task_id):
                self.manager.set_status(task_id, "cancelled")
                return
            self._run_task_locked(task_id, request, workspace, results_dir, generated_dir, patches_dir)

    def _run_task_locked(
        self,
        task_id: str,
        request: TaskCreateRequest,
        workspace: Path,
        results_dir: Path,
        generated_dir: Path,
        patches_dir: Path,
    ) -> None:
        self.manager.set_status(task_id, "running")
        self.manager.add_event(task_id, "sample_uploaded", "sample saved to task workspace")
        self.manager.add_event(task_id, "hardware_resolved", "hardware profile and user overrides prepared")
        self.manager.add_event(task_id, "build_started", "building internal run request")
        original = {
            "WORKSPACE_ROOT": agent_main.WORKSPACE_ROOT,
            "RESULTS_DIR": agent_main.RESULTS_DIR,
            "GENERATED_DIR": agent_main.GENERATED_DIR,
            "PATCHES_DIR": agent_main.PATCHES_DIR,
        }
        try:
            config = build_effective_config(request, workspace, self.settings_path)
            self.manager.add_event(task_id, "correctness_started", "starting correctness and benchmark loop")
            self.manager.add_event(task_id, "benchmark_started", "benchmark command will run after correctness passes")
            if config.profiler.enabled:
                self.manager.add_event(task_id, "profiling_started", "profiler evidence collection enabled")
            agent_main.WORKSPACE_ROOT = workspace
            agent_main.RESULTS_DIR = results_dir
            agent_main.GENERATED_DIR = generated_dir
            agent_main.PATCHES_DIR = patches_dir
            agent_main.run(config, should_cancel=lambda: self.manager.is_cancel_requested(task_id))
            if self.manager.is_cancel_requested(task_id):
                self.manager.set_status(task_id, "cancelled")
            else:
                self.manager.add_event(task_id, "trial_completed", "runner completed trial loop")
                profiler_status = _profiler_status(results_dir)
                if profiler_status in {"benchmark_only", "profiler_metrics_available"}:
                    self.manager.add_event(task_id, "profiler_parsed", f"profiler status: {profiler_status}", {"profiler_status": profiler_status})
                if profiler_status == "profiler_metrics_available" and _read_jsonl(results_dir / "diagnoses.jsonl"):
                    self.manager.add_event(task_id, "diagnosis_completed", "evidence-guided diagnosis completed")
                self.manager.add_event(task_id, "best_updated", "best-seen artifacts generated")
                self.manager.add_event(task_id, "report_generated", "report.md generated")
                self.manager.set_status(task_id, "completed")
        except Exception as exc:
            if self.manager.is_cancel_requested(task_id):
                self.manager.set_status(task_id, "cancelled", str(exc))
            else:
                self.manager.set_status(task_id, "failed", str(exc))
        finally:
            agent_main.WORKSPACE_ROOT = original["WORKSPACE_ROOT"]
            agent_main.RESULTS_DIR = original["RESULTS_DIR"]
            agent_main.GENERATED_DIR = original["GENERATED_DIR"]
            agent_main.PATCHES_DIR = original["PATCHES_DIR"]
