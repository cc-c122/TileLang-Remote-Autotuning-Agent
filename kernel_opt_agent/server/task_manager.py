from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from .models import TaskCreateRequest, TaskRecord, utc_now


class TaskManager:
    def __init__(self, tasks_root: Path):
        self.tasks_root = tasks_root
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def create(self, request: TaskCreateRequest) -> TaskRecord:
        task_id = uuid.uuid4().hex
        workspace = self.tasks_root / task_id
        results_dir = workspace / "results"
        workspace.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)
        record = TaskRecord(task_id=task_id, project_name=request.project_name, workspace=str(workspace), results_dir=str(results_dir))
        self.add_event(task_id, "task_created", "task created", create_if_missing=record)
        return record

    def get(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return task.model_copy(deep=True)

    def add_event(self, task_id: str, event_type: str, message: str, data: dict[str, Any] | None = None, create_if_missing: TaskRecord | None = None) -> None:
        with self._lock:
            if create_if_missing is not None and task_id not in self._tasks:
                self._tasks[task_id] = create_if_missing
            task = self._tasks[task_id]
            task.events.append({"time": utc_now(), "type": event_type, "message": message, "data": data or {}})

    def set_status(self, task_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = status  # type: ignore[assignment]
            if status == "running" and task.started_at is None:
                task.started_at = utc_now()
            if status in {"completed", "failed", "cancelled"}:
                task.finished_at = utc_now()
            task.error = error
            event_type = {
                "completed": "task_completed",
                "failed": "task_failed",
                "cancelled": "task_cancelled",
            }.get(status, "task_status")
            task.events.append({"time": utc_now(), "type": event_type, "message": status, "data": {"error": error}})

    def request_cancel(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self._tasks[task_id]
            task.cancel_requested = True
            task.events.append({"time": utc_now(), "type": "task_cancel_requested", "message": "cancel requested", "data": {}})
            return task.model_copy(deep=True)

    def set_execution_mode(self, task_id: str, execution_mode: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.execution_mode = execution_mode  # type: ignore[assignment]

    def list_recent(self, limit: int = 50) -> list[TaskRecord]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda task: task.created_at, reverse=True)
            return [task.model_copy(deep=True) for task in tasks[:limit]]

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancel_requested)
