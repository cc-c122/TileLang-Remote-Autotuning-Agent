from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ExecutionMode = Literal["parameter_search", "baseline_only", "source_optimization"]


def utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


class SSHSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=22, gt=0, le=65535)
    username: str = ""
    auth_type: Literal["password", "key"] = "password"
    password_env: str | None = "KERNEL_AGENT_SSH_PASSWORD"
    key_path: str | None = None
    remote_workspace: str = "/tmp/kernel_opt_workspace"


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh: SSHSettings = Field(default_factory=SSHSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)


class SamplePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["inline", "path", "upload"] = "inline"
    inline_text: str | None = None
    path: str | None = None
    upload_id: str | None = None
    entry_file: str = "kernel.py"

    @model_validator(mode="after")
    def validate_sample(self) -> "SamplePayload":
        if self.source_type == "inline" and not self.inline_text:
            raise ValueError("sample.inline_text is required for inline samples")
        if self.source_type == "inline" and (self.path is not None or self.upload_id is not None):
            raise ValueError("sample.path and sample.upload_id must be omitted for inline samples")
        if self.source_type == "path" and not self.path:
            raise ValueError("sample.path is required for path samples")
        if self.source_type == "path" and (self.inline_text is not None or self.upload_id is not None):
            raise ValueError("sample.inline_text and sample.upload_id must be omitted for path samples")
        if self.source_type == "upload" and not self.upload_id:
            raise ValueError("sample.upload_id is required for upload samples")
        if self.source_type == "upload" and (self.inline_text is not None or self.path is not None):
            raise ValueError("sample.inline_text and sample.path must be omitted for upload samples")
        return self


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_command: str | None = "python -m py_compile kernel.py"
    correctness_command: str = "python correctness.py"
    benchmark_command: str = "python benchmark.py"


class BudgetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["rule_based"] = "rule_based"
    max_iterations: int = Field(default=1, ge=1)
    candidates_per_iteration: int = Field(default=1, gt=0)
    timeout_seconds: int = Field(default=60, gt=0)
    objective: Literal["latency"] = "latency"

    @model_validator(mode="before")
    @classmethod
    def validate_positive_budget(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        checks = {
            "max_iterations": "max_iterations must be >= 1",
            "candidates_per_iteration": "candidates_per_iteration must be >= 1",
            "timeout_seconds": "timeout_seconds must be >= 1",
        }
        for field_name, message in checks.items():
            value = data.get(field_name)
            if value is not None and value < 1:
                raise ValueError(message)
        return data


class ProfilerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    type: Literal["dummy"] = "dummy"


class PatchingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    run_controlled_trial: bool = False


class RunnerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["local", "ssh"] = "local"


class OptimizationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_candidates: int = Field(default=3, ge=1)
    benchmark_repeats: int = Field(default=5, ge=1)
    min_improvement_percent: float = Field(default=1.0, ge=0.0)


class HardwareTargetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_model: str = "unknown"
    backend: str = "unknown"
    user_overrides: dict[str, Any] = Field(default_factory=dict)
    allow_llm_lookup: bool = False
    allow_web_lookup: bool = False


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = "web-task"
    sample: SamplePayload
    target: HardwareTargetPayload = Field(default_factory=HardwareTargetPayload)
    commands: CommandPayload = Field(default_factory=CommandPayload)
    budget: BudgetPayload = Field(default_factory=BudgetPayload)
    profiler: ProfilerPayload = Field(default_factory=ProfilerPayload)
    patching: PatchingPayload = Field(default_factory=PatchingPayload)
    runner: RunnerPayload = Field(default_factory=RunnerPayload)
    optimization: OptimizationPayload = Field(default_factory=OptimizationPayload)


class HardwareResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_model: str = "unknown"
    backend: str = "unknown"
    user_overrides: dict[str, Any] = Field(default_factory=dict)
    allow_llm_lookup: bool = False
    allow_web_lookup: bool = False


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    project_name: str = "web-task"
    status: TaskStatus = "pending"
    created_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    workspace: str
    results_dir: str
    execution_mode: ExecutionMode | None = None
    execution_mode_reason: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    cancel_requested: bool = False
