from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from kernel_opt_agent.config_model import AppConfig, safe_config_dict
from kernel_opt_agent.hardware.profile_loader import normalize_profile_name


DEFAULT_SEARCH_VALUES: dict[str, list[Any]] = {
    "BM": [16, 32],
    "BN": [32, 64],
    "BK": [32],
    "NUM_THREADS": [128],
    "NUM_STAGES": [2],
    "VECTOR_WIDTH": [1, 2],
    "UNROLL_FACTOR": [1],
    "USE_SHARED": [True],
    "USE_DOUBLE_BUFFER": [True],
}


class OptimizationBudget(BaseModel):
    strategy: Literal["grid", "random", "llm", "rule_based", "hybrid"] = "rule_based"
    max_iterations: int = Field(default=1, gt=0)
    candidates_per_iteration: int = Field(default=3, gt=0)
    timeout_seconds: int = Field(default=60, gt=0)
    objective: Literal["latency", "tflops"] = "latency"
    random_seed: int = 20260701


class RunRequest(BaseModel):
    project_name: str = "tilelang-agent-run-request"
    sample_path: str
    entry_file: str = "kernel.py"
    gpu_model: str | None = None
    build_command: str | None = None
    correctness_command: str
    benchmark_command: str
    optimization_budget: OptimizationBudget = Field(default_factory=OptimizationBudget)
    hardware_overrides: dict[str, Any] = Field(default_factory=dict)
    search_space: dict[str, list[Any]] | None = None


def _load_mapping(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).expanduser()
    if not request_path.is_absolute():
        request_path = (Path.cwd() / request_path).resolve()
    text = request_path.read_text(encoding="utf-8")
    lowered = text.lower()
    for key in ("api_key:", "password:", "token:"):
        if key in lowered:
            raise ValueError(f"sensitive value field is not allowed in run request: {key.rstrip(':')}")
    return yaml.safe_load(text) or {}


def load_run_request(path: str | Path) -> RunRequest:
    return RunRequest.model_validate(_load_mapping(path))


def _resolve_sample_path(sample_path: str) -> Path:
    path = Path(sample_path).expanduser()
    if not path.is_absolute():
        path = (Path(__file__).resolve().parent / path).resolve()
    return path


def _template_placeholders(sample_path: str, entry_file: str) -> set[str]:
    resolved = _resolve_sample_path(sample_path)
    template = resolved / entry_file if resolved.is_dir() else resolved
    if not template.exists():
        raise ValueError(f"run request entry_file not found: {template}")
    text = template.read_text(encoding="utf-8")
    return set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", text))


def _infer_search_space(request: RunRequest) -> dict[str, list[Any]]:
    placeholders = _template_placeholders(request.sample_path, request.entry_file)
    if request.search_space is not None:
        return request.search_space
    return {name: DEFAULT_SEARCH_VALUES.get(name, [1]) for name in sorted(placeholders)}


def app_config_from_run_request(request: RunRequest) -> AppConfig:
    budget = request.optimization_budget
    gpu_model = request.gpu_model.strip() if request.gpu_model else None
    raw_config: dict[str, Any] = {
        "project_name": request.project_name,
        "runner": {"type": "local"},
        "remote": {
            "host": "127.0.0.1",
            "port": 22,
            "username": "user",
            "auth_type": "key",
            "key_path": "~/.ssh/id_rsa",
            "password_env": "KERNEL_AGENT_SSH_PASSWORD",
            "remote_workspace": "/tmp/kernel_opt_workspace",
        },
        "kernel": {
            "sample_path": request.sample_path,
            "entry_file": request.entry_file,
            "build_command": request.build_command,
            "correctness_command": request.correctness_command,
            "run_command": request.benchmark_command,
        },
        "search": budget.model_dump(),
        "search_space": _infer_search_space(request),
        "hardware": {
            "target_name": gpu_model,
            "profile": normalize_profile_name(gpu_model) if gpu_model else None,
            "fields": request.hardware_overrides,
            "fields_source": "user_override",
        },
    }
    return AppConfig.model_validate(raw_config)


def load_config_from_run_request(path: str | Path) -> AppConfig:
    return app_config_from_run_request(load_run_request(path))


def write_effective_config_from_run_request(request: RunRequest, output_path: str | Path) -> AppConfig:
    config = app_config_from_run_request(request)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(safe_config_dict(config), sort_keys=True), encoding="utf-8")
    return config
