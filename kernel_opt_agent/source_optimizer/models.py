from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SOURCE_OPTIMIZATION_SCHEMA_VERSION = "v2.source_optimization.v1"
SourceOptimizationStatus = Literal[
    "not_requested",
    "unavailable",
    "running",
    "completed",
    "failed",
    "cancelled",
]
SourceTrialStatus = Literal[
    "proposed",
    "running",
    "validation_failed",
    "build_failed",
    "correctness_failed",
    "benchmark_failed",
    "no_improvement",
    "accepted",
    "cancelled",
]


class SourceTrialCorrectness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool | None = None
    max_error: float | None = None
    reason: str | None = None


class SourceOptimizationTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial_id: str
    optimization_name: str
    target_file: str
    target_function: str
    hypothesis: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_before_sha256: str | None = None
    source_after_sha256: str | None = None
    status: SourceTrialStatus = "proposed"
    decision_reason: str | None = None
    diff: str = ""
    correctness: SourceTrialCorrectness = Field(default_factory=SourceTrialCorrectness)
    baseline_latency_ms: float | None = None
    candidate_latency_ms: float | None = None
    baseline_samples_ms: list[float] = Field(default_factory=list)
    candidate_samples_ms: list[float] = Field(default_factory=list)
    improvement_percent: float | None = None
    rollback_verified: bool = False
    execution_source_sha256: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)


class SourceOptimizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v2.source_optimization.v1"] = SOURCE_OPTIMIZATION_SCHEMA_VERSION
    status: SourceOptimizationStatus = "not_requested"
    reason: str | None = None
    baseline_source_sha256: str | None = None
    best_source_sha256: str | None = None
    accepted_trial_id: str | None = None
    trials: list[SourceOptimizationTrial] = Field(default_factory=list)
