from __future__ import annotations

import threading
from pathlib import Path

import kernel_opt_agent.main as agent_main

from .models import TaskCreateRequest
from .profiler_status import profiler_status, read_jsonl
from .run_request_builder import build_effective_config
from .task_manager import TaskManager


WORKER_LOCK = threading.Lock()


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
                status = profiler_status(
                    read_jsonl(results_dir / "profiler_results.jsonl"),
                    read_jsonl(results_dir / "metric_observations.jsonl"),
                )
                if status in {"benchmark_only", "profiler_metrics_available"}:
                    self.manager.add_event(task_id, "profiler_parsed", f"profiler status: {status}", {"profiler_status": status})
                if status == "profiler_metrics_available" and read_jsonl(results_dir / "diagnoses.jsonl"):
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
