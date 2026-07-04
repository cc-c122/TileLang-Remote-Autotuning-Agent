from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kernel_opt_agent.config_model import AppConfig, safe_config_dict
from kernel_opt_agent.hardware.profile_loader import normalize_profile_name


SCHEMA_VERSION = "v2.run_request.v1"
PACKAGE_ROOT = Path(__file__).resolve().parent
RUN_REQUEST_WORKSPACE = PACKAGE_ROOT / "workspace" / "run_requests"

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


class SampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["inline", "path", "upload"]
    inline_text: str | None = None
    path: str | None = None
    entry_file: str = "kernel.py"

    @model_validator(mode="after")
    def validate_sample(self) -> "SampleRequest":
        _safe_entry_file(self.entry_file)
        if self.source_type == "inline" and not self.inline_text:
            raise ValueError("sample.inline_text is required when source_type=inline")
        if self.source_type == "inline" and self.path is not None:
            raise ValueError("sample.path must be null when source_type=inline")
        if self.source_type in {"path", "upload"} and not self.path:
            raise ValueError("sample.path is required when source_type=path or upload")
        if self.source_type in {"path", "upload"} and self.inline_text:
            raise ValueError("sample.inline_text must be null or empty when source_type=path or upload")
        return self


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_command: str
    correctness_command: str
    benchmark_command: str


class TargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_model: str
    backend: str = "unknown"


class SettingsRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_saved_settings: bool = True


class HardwareOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["rule_based"] = "rule_based"
    max_iterations: int = Field(default=1, gt=0)
    candidates_per_iteration: int = Field(default=3, gt=0)
    timeout_seconds: int = Field(default=60, gt=0)
    objective: Literal["latency"] = "latency"


class ProfilerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    type: Literal["dummy"] = "dummy"


class PatchingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2.run_request.v1"]
    project_name: str = "tilelang-agent-run-request"
    sample: SampleRequest
    commands: CommandRequest
    target: TargetRequest
    settings_ref: SettingsRef = Field(default_factory=SettingsRef)
    hardware_overrides: HardwareOverrides = Field(default_factory=HardwareOverrides)
    search: SearchRequest = Field(default_factory=SearchRequest)
    profiler: ProfilerRequest = Field(default_factory=ProfilerRequest)
    patching: PatchingRequest = Field(default_factory=PatchingRequest)


def _safe_entry_file(entry_file: str) -> None:
    path = Path(entry_file)
    if path.is_absolute() or ".." in path.parts or not entry_file or entry_file.endswith(("/", "\\")):
        raise ValueError("sample.entry_file must be a relative file path inside the sample workspace")


def _safe_project_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("._-")
    return slug or "run_request"


def _assert_within(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise ValueError(f"refusing path outside workspace: {child}")


def _load_mapping(path: str | Path) -> dict[str, Any]:
    request_path = Path(path).expanduser()
    if not request_path.is_absolute():
        request_path = (Path.cwd() / request_path).resolve()
    text = request_path.read_text(encoding="utf-8")
    lowered = text.lower()
    for key in ("api_key:", "password:", "token:"):
        if key in lowered:
            raise ValueError(f"sensitive value field is not allowed in run request: {key.rstrip(':')}")
    raw = yaml.safe_load(text) or {}
    raw["_request_dir"] = str(request_path.parent)
    return raw


def load_run_request(path: str | Path) -> RunRequest:
    raw = _load_mapping(path)
    raw.pop("_request_dir", None)
    return RunRequest.model_validate(raw)


def _request_dir(raw: dict[str, Any] | None) -> Path:
    if raw and raw.get("_request_dir"):
        return Path(str(raw["_request_dir"]))
    return Path.cwd()


def _materialize_inline_sample(request: RunRequest) -> str:
    sample_dir = RUN_REQUEST_WORKSPACE / _safe_project_slug(request.project_name) / "sample"
    entry_path = sample_dir / request.sample.entry_file
    _assert_within(entry_path, sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry_path.write_text(request.sample.inline_text or "", encoding="utf-8")
    return str(sample_dir)


def _resolve_path_sample(request: RunRequest, request_dir: Path) -> str:
    if not request.sample.path:
        raise ValueError("sample.path is required")
    path = Path(request.sample.path).expanduser()
    if not path.is_absolute():
        path = (request_dir / path).resolve()
    if not path.exists():
        raise ValueError(f"sample.path does not exist: {path}")
    return str(path)


def _materialize_sample(request: RunRequest, request_dir: Path) -> str:
    if request.sample.source_type == "inline":
        return _materialize_inline_sample(request)
    if request.sample.source_type == "path":
        return _resolve_path_sample(request, request_dir)
    if request.sample.inline_text:
        return _materialize_inline_sample(request)
    return _resolve_path_sample(request, request_dir)


def _resolve_sample_path(sample_path: str) -> Path:
    path = Path(sample_path).expanduser()
    if not path.is_absolute():
        path = (PACKAGE_ROOT / path).resolve()
    return path


def _template_placeholders(sample_path: str, entry_file: str) -> set[str]:
    resolved = _resolve_sample_path(sample_path)
    template = resolved / entry_file if resolved.is_dir() else resolved
    if not template.exists():
        raise ValueError(f"run request entry_file not found: {template}")
    text = template.read_text(encoding="utf-8")
    return set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", text))


def _infer_search_space(request: RunRequest, sample_path: str) -> dict[str, list[Any]]:
    placeholders = _template_placeholders(sample_path, request.sample.entry_file)
    return {name: DEFAULT_SEARCH_VALUES.get(name, [1]) for name in sorted(placeholders)}


def _app_config_from_request(request: RunRequest, request_dir: Path) -> AppConfig:
    sample_path = _materialize_sample(request, request_dir)
    gpu_model = request.target.gpu_model.strip() if request.target.gpu_model else None
    backend = request.target.backend if request.target.backend and request.target.backend != "unknown" else None
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
            "sample_path": sample_path,
            "entry_file": request.sample.entry_file,
            "build_command": request.commands.build_command,
            "correctness_command": request.commands.correctness_command,
            "run_command": request.commands.benchmark_command,
        },
        "search": request.search.model_dump(exclude={"search_space"}),
        "search_space": _infer_search_space(request, sample_path),
        "hardware": {
            "target_name": gpu_model,
            "backend": backend,
            "profile": normalize_profile_name(gpu_model) if gpu_model else None,
            "fields": request.hardware_overrides.fields,
            "fields_source": "user_override",
        },
        "profiler": request.profiler.model_dump(),
        "patching": request.patching.model_dump(),
    }
    return AppConfig.model_validate(raw_config)


def app_config_from_run_request(request: RunRequest) -> AppConfig:
    return _app_config_from_request(request, Path.cwd())


def load_config_from_run_request(path: str | Path) -> AppConfig:
    raw = _load_mapping(path)
    request_dir = _request_dir(raw)
    raw.pop("_request_dir", None)
    return _app_config_from_request(RunRequest.model_validate(raw), request_dir)


def write_effective_config_from_run_request(request: RunRequest, output_path: str | Path) -> AppConfig:
    config = app_config_from_run_request(request)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(safe_config_dict(config), sort_keys=True), encoding="utf-8")
    return config
