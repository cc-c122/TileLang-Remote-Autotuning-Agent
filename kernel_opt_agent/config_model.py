from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


ROOT = Path(__file__).resolve().parent


class RunnerConfig(BaseModel):
    type: Literal["ssh", "local"] = "local"


class RemoteConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 22
    username: str = ""
    auth_type: Literal["password", "key"] = "key"
    key_path: str | None = None
    password_env: str | None = None
    remote_workspace: str = "/tmp/kernel_opt_workspace"

    @model_validator(mode="after")
    def validate_remote(self) -> "RemoteConfig":
        if not self.remote_workspace.startswith("/"):
            raise ValueError("remote.remote_workspace must be an absolute path")
        if self.auth_type == "key" and not self.key_path:
            raise ValueError("remote.key_path is required when auth_type=key")
        if self.auth_type == "password" and not self.password_env:
            raise ValueError("remote.password_env is required when auth_type=password")
        return self


class LLMConfig(BaseModel):
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 2048


class KernelConfig(BaseModel):
    sample_path: str
    entry_file: str
    build_command: str | None = None
    run_command: str
    correctness_command: str


class SearchConfig(BaseModel):
    strategy: Literal["grid", "random", "llm", "rule_based", "hybrid"] = "hybrid"
    max_iterations: int = Field(default=5, gt=0)
    candidates_per_iteration: int = Field(default=4, gt=0)
    timeout_seconds: int = Field(default=300, gt=0)
    objective: Literal["latency", "tflops"] = "latency"
    random_seed: int = 20260701


class MetricsConfig(BaseModel):
    latency_regex: str | None = r"latency\s*[:=]\s*([0-9.]+)"
    tflops_regex: str | None = r"tflops\s*[:=]\s*([0-9.]+)"
    bandwidth_regex: str | None = r"bandwidth\s*[:=]\s*([0-9.]+)"


class ConstraintsConfig(BaseModel):
    max_shared_memory: int | None = None
    max_registers: int | None = None
    allowed_file_patterns: list[str] = Field(default_factory=lambda: ["*.py", "*.yaml", "*.json", "*.txt"])
    denied_commands: list[str] = Field(default_factory=list)


class HardwareConfig(BaseModel):
    target_name: str | None = None
    backend: str | None = None
    profile: str | None = None
    allow_doc_lookup: bool = False
    doc_paths: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)
    fields_source: Literal["user_config", "user_override"] = "user_config"


class HardwareDetectionConfig(BaseModel):
    enabled: bool = True
    remote_detection: bool = True
    builtin_profile: bool = True
    doc_lookup: bool = False
    safe_probe: bool = True
    conservative_unknown_mode: bool = True
    timeout_seconds: int = Field(default=60, gt=0)


class ProfilerConfig(BaseModel):
    enabled: bool = True
    type: str = "dummy"


class PatchingConfig(BaseModel):
    enabled: bool = False


class AppConfig(BaseModel):
    project_name: str = "tilelang-autotune-demo"
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    kernel: KernelConfig
    search: SearchConfig = Field(default_factory=SearchConfig)
    search_space: dict[str, list[Any]]
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    hardware_detection: HardwareDetectionConfig = Field(default_factory=HardwareDetectionConfig)
    profiler: ProfilerConfig = Field(default_factory=ProfilerConfig)
    patching: PatchingConfig = Field(default_factory=PatchingConfig)

    @model_validator(mode="after")
    def validate_app(self) -> "AppConfig":
        if not self.search_space:
            raise ValueError("search_space is required and must be non-empty")
        for name, values in self.search_space.items():
            if not name or not isinstance(values, list) or not values:
                raise ValueError(f"search_space.{name} must be a non-empty list")
        if any(k in os.environ for k in ("OPENAI_API_KEY_VALUE", "SSH_PASSWORD")):
            pass
        sample = resolve_path(self.kernel.sample_path)
        if not sample.exists():
            raise ValueError(f"kernel.sample_path does not exist: {sample}")
        entry = sample / self.kernel.entry_file if sample.is_dir() else sample
        if sample.is_dir() and not entry.exists():
            raise ValueError(f"kernel.entry_file not found under sample_path: {entry}")
        if sample.is_file() and Path(self.kernel.entry_file).name != sample.name:
            raise ValueError("when sample_path is a file, kernel.entry_file must match that file name")
        template_text = entry.read_text(encoding="utf-8")
        placeholders = set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", template_text))
        space_names = set(self.search_space.keys())
        if placeholders != space_names:
            missing = sorted(placeholders - space_names)
            extra = sorted(space_names - placeholders)
            raise ValueError(
                "template placeholders must exactly match search_space keys; "
                f"missing={missing}, extra={extra}"
            )
        return self


def resolve_path(path: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    return p


def load_config(path: str) -> AppConfig:
    cfg_path = Path(path).expanduser()
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    text = cfg_path.read_text(encoding="utf-8")
    forbidden_keys = ("api_key:", "password:", "token:")
    lowered = text.lower()
    for key in forbidden_keys:
        if key in lowered:
            raise ValueError(f"sensitive value field is not allowed in config: {key.rstrip(':')}")
    return AppConfig.model_validate(raw)


def safe_config_dict(config: AppConfig) -> dict[str, Any]:
    data = config.model_dump()
    data["llm"]["api_key_env"] = config.llm.api_key_env
    if data.get("remote", {}).get("key_path"):
        data["remote"]["key_path"] = "<redacted:key_path>"
    data["remote"]["password_env"] = config.remote.password_env
    return data
