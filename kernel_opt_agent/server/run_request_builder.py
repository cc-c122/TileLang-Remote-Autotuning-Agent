from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kernel_opt_agent.config_model import AppConfig, safe_config_dict
from kernel_opt_agent.run_request import RunRequest, app_config_from_run_request

from .models import SettingsPayload, TaskCreateRequest

if TYPE_CHECKING:
    from .sample_uploads import SampleUploadStore


def _assert_within(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise ValueError(f"refusing path outside task workspace: {child}")


def _copy_sample_tree(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst / src.name)
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def materialize_sample(
    request: TaskCreateRequest,
    task_workspace: Path,
    upload_store: SampleUploadStore | None = None,
) -> Path:
    sample_dir = task_workspace / "sample"
    _assert_within(sample_dir, task_workspace)
    entry_path = sample_dir / request.sample.entry_file
    _assert_within(entry_path, sample_dir)
    if request.sample.source_type == "inline":
        sample_dir.mkdir(parents=True, exist_ok=True)
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(request.sample.inline_text or "", encoding="utf-8")
        return sample_dir
    if request.sample.source_type == "upload":
        if upload_store is None:
            raise ValueError("sample upload storage is not configured")
        return upload_store.materialize(
            request.sample.upload_id or "",
            request.sample.entry_file,
            sample_dir,
        )
    source_path = Path(request.sample.path or "").expanduser()
    if not source_path.is_absolute():
        source_path = (Path.cwd() / source_path).resolve()
    if not source_path.exists():
        raise ValueError(f"sample.path does not exist: {source_path}")
    _copy_sample_tree(source_path, sample_dir)
    if not entry_path.exists():
        raise ValueError(f"sample.entry_file not found after upload: {entry_path}")
    return sample_dir


def build_internal_run_request(
    request: TaskCreateRequest,
    task_workspace: Path,
    upload_store: SampleUploadStore | None = None,
) -> RunRequest:
    sample_dir = materialize_sample(request, task_workspace, upload_store)
    raw: dict[str, Any] = {
        "schema_version": "v2.run_request.v1",
        "project_name": request.project_name,
        "sample": {
            "source_type": "path",
            "inline_text": None,
            "path": str(sample_dir),
            "entry_file": request.sample.entry_file,
        },
        "commands": {
            "build_command": request.commands.build_command or "",
            "correctness_command": request.commands.correctness_command,
            "benchmark_command": request.commands.benchmark_command,
        },
        "target": {
            "gpu_model": request.target.gpu_model,
            "backend": request.target.backend,
        },
        "settings_ref": {"use_saved_settings": request.runner.type == "ssh"},
        "hardware_overrides": {"fields": request.target.user_overrides},
        "search": {
            "strategy": request.budget.strategy,
            "max_iterations": request.budget.max_iterations,
            "candidates_per_iteration": request.budget.candidates_per_iteration,
            "timeout_seconds": request.budget.timeout_seconds,
            "objective": request.budget.objective,
        },
        "profiler": request.profiler.model_dump(),
        "patching": request.patching.model_dump(),
    }
    run_request = RunRequest.model_validate(raw)
    (task_workspace / "run_request.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return run_request


def _load_server_settings(settings_path: Path | None) -> SettingsPayload:
    if settings_path is None or not settings_path.exists():
        return SettingsPayload()
    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    return SettingsPayload.model_validate(raw)


def _apply_server_settings(config: AppConfig, settings: SettingsPayload) -> None:
    config.remote.host = settings.ssh.host
    config.remote.port = settings.ssh.port
    config.remote.username = settings.ssh.username
    config.remote.auth_type = settings.ssh.auth_type
    config.remote.password_env = settings.ssh.password_env
    config.remote.key_path = settings.ssh.key_path
    config.remote.remote_workspace = settings.ssh.remote_workspace
    config.llm.provider = "openai_compatible"
    config.llm.base_url = settings.llm.base_url
    config.llm.model = settings.llm.model
    config.llm.api_key_env = settings.llm.api_key_env


def build_effective_config(
    request: TaskCreateRequest,
    task_workspace: Path,
    settings_path: Path | None = None,
    upload_store: SampleUploadStore | None = None,
) -> AppConfig:
    run_request = build_internal_run_request(request, task_workspace, upload_store)
    run_request.settings_ref.use_saved_settings = False
    config = app_config_from_run_request(run_request)
    config.runner.type = request.runner.type
    if request.runner.type == "ssh":
        _apply_server_settings(config, _load_server_settings(settings_path))
        config.hardware_detection.remote_detection = True
        config.hardware_detection.safe_probe = True
    else:
        config.hardware_detection.remote_detection = False
        config.hardware_detection.safe_probe = False
    (task_workspace / "effective_config.yaml").write_text(yaml.safe_dump(safe_config_dict(config), sort_keys=True), encoding="utf-8")
    return config
