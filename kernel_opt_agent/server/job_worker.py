from __future__ import annotations

import os
import threading
import json
from contextlib import nullcontext
from pathlib import Path

import kernel_opt_agent.main as agent_main
import yaml
from kernel_opt_agent.agent.llm_client import OpenAICompatibleClient
from kernel_opt_agent.config_model import safe_config_dict
from kernel_opt_agent.file_task import FileTaskError
from kernel_opt_agent.source_optimizer import (
    SourceOptimizationResult,
    inspect_source_optimization,
    read_source_result,
    redact_source_text,
    run_source_optimization,
    write_source_result,
)
from kernel_opt_agent.source_optimizer.analyzer import sha256_file
from kernel_opt_agent.runner.ssh_runtime import bind_ssh_context

from .models import TaskCreateRequest
from .profiler_status import profiler_status, read_jsonl
from .run_request_builder import build_effective_config
from .sample_uploads import SampleUploadStore
from .task_manager import TaskManager
from .task_preflight import run_file_preflight


WORKER_LOCK = threading.Lock()


class JobWorker:
    def __init__(
        self,
        manager: TaskManager,
        settings_path: Path | None = None,
        upload_store: SampleUploadStore | None = None,
    ):
        self.manager = manager
        self.settings_path = settings_path
        self.upload_store = upload_store

    def submit(self, task_id: str, request: TaskCreateRequest, execution_context=None) -> None:
        thread = threading.Thread(target=self._run_task, args=(task_id, request, execution_context), daemon=True)
        thread.start()

    def _run_task(self, task_id: str, request: TaskCreateRequest, execution_context=None) -> None:
        task = self.manager.get(task_id)
        workspace = Path(task.workspace)
        results_dir = Path(task.results_dir)
        generated_dir = workspace / "generated"
        patches_dir = workspace / "patches"
        self.manager.add_event(task_id, "task_queued", "waiting for runner slot")
        try:
            with WORKER_LOCK, (execution_context.operation() if execution_context else nullcontext()), bind_ssh_context(execution_context):
                if self.manager.is_cancel_requested(task_id):
                    if request.optimization.enabled:
                        write_source_result(results_dir, SourceOptimizationResult(status="cancelled", reason="cancelled before execution"))
                    self.manager.set_status(task_id, "cancelled")
                    return
                if execution_context:
                    execution_context.prepare_task(task_id)
                self._run_task_locked(task_id, request, workspace, results_dir, generated_dir, patches_dir, execution_context)
        except Exception as exc:
            message = getattr(exc, "code", "ssh_task_preflight_failed")
            (results_dir / "connection_failure.json").write_text(json.dumps({"stage": "task_preflight", "error_category": message, "exception_type": type(exc).__name__}), encoding="utf-8")
            self.manager.set_status(task_id, "failed", message)
        finally:
            if execution_context:
                execution_context.release()

    def _run_task_locked(
        self,
        task_id: str,
        request: TaskCreateRequest,
        workspace: Path,
        results_dir: Path,
        generated_dir: Path,
        patches_dir: Path,
        execution_context=None,
    ) -> None:
        self.manager.set_status(task_id, "running")
        self.manager.add_event(task_id, "hardware_resolved", "hardware profile and user overrides prepared")
        original = {
            "WORKSPACE_ROOT": agent_main.WORKSPACE_ROOT,
            "RESULTS_DIR": agent_main.RESULTS_DIR,
            "GENERATED_DIR": agent_main.GENERATED_DIR,
            "PATCHES_DIR": agent_main.PATCHES_DIR,
        }
        try:
            config = build_effective_config(request, workspace, self.settings_path, self.upload_store, execution_context=execution_context)
            self.manager.add_event(task_id, "sample_uploaded", "sample saved to isolated task workspace")
            source_entry = None
            source_analysis = None
            if request.optimization.enabled:
                source_entry, source_analysis = inspect_source_optimization(config)
                if source_entry is None or not source_analysis.targets:
                    config.execution_mode = "baseline_only"
                    reason = source_analysis.reason or "source optimization target is unavailable"
                    self.manager.set_execution_mode(task_id, config.execution_mode, reason)
                    write_source_result(
                        results_dir,
                        SourceOptimizationResult(
                            status="unavailable",
                            reason=reason,
                            baseline_source_sha256=sha256_file(source_entry) if source_entry else None,
                            best_source_sha256=sha256_file(source_entry) if source_entry else None,
                        ),
                    )
                else:
                    config.execution_mode = "source_optimization"
                    self.manager.set_execution_mode(task_id, config.execution_mode)
                    source_hash = sha256_file(source_entry)
                    write_source_result(
                        results_dir,
                        SourceOptimizationResult(
                            status="running",
                            baseline_source_sha256=source_hash,
                            best_source_sha256=source_hash,
                        ),
                    )
            else:
                self.manager.set_execution_mode(task_id, config.execution_mode)
            (workspace / "effective_config.yaml").write_text(
                yaml.safe_dump(safe_config_dict(config), sort_keys=True),
                encoding="utf-8",
            )
            if config.file_task:
                self.manager.add_event(task_id, "preflight_started", "checking Python, dependencies, device binding and protected files")
                run_file_preflight(config, results_dir, lambda: self.manager.is_cancel_requested(task_id), execution_context)
                if self.manager.is_cancel_requested(task_id):
                    if request.optimization.enabled:
                        write_source_result(results_dir, SourceOptimizationResult(status="cancelled", reason="cancelled during preflight"))
                    self.manager.set_status(task_id, "cancelled")
                    return
                self.manager.add_event(task_id, "preflight_completed", "file task preflight passed")
            agent_main.WORKSPACE_ROOT = workspace
            agent_main.RESULTS_DIR = results_dir
            agent_main.GENERATED_DIR = generated_dir
            agent_main.PATCHES_DIR = patches_dir
            agent_main.run(
                config,
                should_cancel=lambda: self.manager.is_cancel_requested(task_id),
                event_callback=lambda event_type, message: self.manager.add_event(task_id, event_type, message),
            )
            if config.file_task:
                records = read_jsonl(results_dir / "experiments.jsonl")
                failures = [row for row in records if row.get("status") not in {"benchmark_ok", "cancelled"}]
                if failures:
                    failure = failures[-1]
                    raise FileTaskError(failure["status"], (failure.get("error") or {}).get("message") or "file task baseline failed")
                if self.manager.is_cancel_requested(task_id):
                    if request.optimization.enabled:
                        write_source_result(results_dir, SourceOptimizationResult(status="cancelled", reason="cancelled during baseline; rollback verified"))
                    self.manager.set_status(task_id, "cancelled")
                    return
            if execution_context:
                execution_context.verify_bound_device()
            if request.optimization.enabled and source_entry is not None and source_analysis is not None:
                if source_analysis.targets:
                    self.manager.add_event(task_id, "source_optimization_started", "running controlled source optimization trial")
                planning_client = None
                if os.environ.get(config.llm.api_key_env):
                    planning_client = OpenAICompatibleClient(
                        config.llm.base_url,
                        config.llm.api_key_env,
                        config.llm.model,
                        config.llm.temperature,
                        config.llm.max_tokens,
                    )
                source_result = run_source_optimization(
                    config,
                    workspace,
                    results_dir,
                    source_analysis,
                    source_entry,
                    should_cancel=lambda: self.manager.is_cancel_requested(task_id),
                    event_callback=lambda event_type, message: self.manager.add_event(task_id, event_type, message),
                    planning_client=planning_client,
                )
                self.manager.add_event(
                    task_id,
                    "source_optimization_unavailable" if source_result.status == "unavailable" else "source_optimization_completed",
                    source_result.reason or source_result.status,
                    {"status": source_result.status, "accepted_trial_id": source_result.accepted_trial_id},
                )
                if source_result.status in {"failed", "cancelled"}:
                    error = (source_result.reason or "source baseline failed") if source_result.status == "failed" else None
                    self.manager.set_status(task_id, source_result.status, error)
                    return
            elif config.file_task and request.optimization.enabled and source_analysis is not None:
                self.manager.add_event(task_id, "source_optimization_unavailable", source_analysis.reason or "no authorized source transformation")
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
                trial_records = read_jsonl(results_dir / "experiments.jsonl")
                has_usable_best = any(
                    record.get("status") == "benchmark_ok"
                    and (record.get("correctness") or {}).get("passed") is True
                    for record in trial_records
                )
                if has_usable_best:
                    self.manager.add_event(task_id, "best_updated", "usable best artifacts generated")
                if (results_dir / "report.md").is_file():
                    self.manager.add_event(task_id, "report_generated", "report.md generated")
                self.manager.set_status(task_id, "completed")
        except Exception as exc:
            public_error = redact_source_text(config, str(exc)) if "config" in locals() else "task failed before effective configuration was available"
            if isinstance(exc, FileTaskError) and "config" not in locals():
                public_error = exc.code + ": file task input is incompatible"
            if request.test_files:
                category = getattr(exc, "code", "file_task_failed")
                (results_dir / "file_task_failure.json").write_text(json.dumps({"error_category": category, "message": public_error, "exception_type": type(exc).__name__}), encoding="utf-8")
                self.manager.add_event(task_id, "preflight_failed" if not (results_dir / "experiments.jsonl").exists() else "execution_failed", category)
            if request.optimization.enabled:
                source_result = SourceOptimizationResult.model_validate(read_source_result(results_dir))
                source_result.status = "failed"
                source_result.reason = public_error
                write_source_result(results_dir, source_result)
            self.manager.set_status(task_id, "failed", public_error)
        finally:
            agent_main.WORKSPACE_ROOT = original["WORKSPACE_ROOT"]
            agent_main.RESULTS_DIR = original["RESULTS_DIR"]
            agent_main.GENERATED_DIR = original["GENERATED_DIR"]
            agent_main.PATCHES_DIR = original["PATCHES_DIR"]
