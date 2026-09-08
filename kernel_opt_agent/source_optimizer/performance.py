from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from typing import Literal


_CODEGEN_RECORD = re.compile(r"^GENERATED_SOURCE_SHA256=([0-9a-fA-F]{64})[ \t]*$", re.MULTILINE)
MINIMUM_REPEATS = 3


@dataclass(frozen=True)
class CodegenEvidence:
    sha256: str | None = None
    status: Literal["available", "unavailable", "inconsistent"] = "unavailable"
    source: str = "build_log.GENERATED_SOURCE_SHA256"


def parse_codegen_evidence(build_log: str) -> CodegenEvidence:
    # This optional harness record is counterevidence, not proof of binary equivalence.
    records = [line.strip() for line in build_log.splitlines() if line.strip().startswith("GENERATED_SOURCE_SHA256=")]
    if not records:
        return CodegenEvidence()
    hashes = set()
    for record in records:
        match = _CODEGEN_RECORD.fullmatch(record)
        if match is None:
            return CodegenEvidence(status="inconsistent")
        hashes.add(match.group(1).lower())
    if len(hashes) != 1:
        return CodegenEvidence(status="inconsistent")
    return CodegenEvidence(sha256=next(iter(hashes)), status="available")


@dataclass
class PerformanceGate:
    decision: Literal["accepted", "no_improvement", "inconclusive", "codegen_unchanged"]
    reason: str
    schema_version: str = "v2.performance_gate.v1"
    method: str = "non_overlapping_repeat_envelope_with_baseline_recheck"
    minimum_repeats: int = MINIMUM_REPEATS
    threshold_percent: float = 0.0
    median_improvement_percent: float | None = None
    conservative_improvement_percent: float | None = None
    baseline_codegen_hash: str | None = None
    candidate_codegen_hash: str | None = None
    codegen_status: str = "unavailable"
    codegen_source: str = "build_log.GENERATED_SOURCE_SHA256"
    baseline_samples_ms: list[float] = field(default_factory=list)
    candidate_samples_ms: list[float] = field(default_factory=list)
    baseline_recheck_samples_ms: list[float] = field(default_factory=list)
    baseline_recheck_passed: bool = False
    uncertainty: list[str] = field(default_factory=lambda: [
        "The observed repeat envelope is a conservative screen, not a statistical confidence interval.",
        "Timing alone does not establish a hardware bottleneck or guarantee reproducible speedup on other workloads.",
    ])

    def to_dict(self) -> dict:
        return asdict(self)


def assess_performance(
    baseline: list[float],
    candidate: list[float],
    threshold_percent: float,
    baseline_codegen: CodegenEvidence | None = None,
    candidate_codegen: CodegenEvidence | None = None,
    baseline_recheck: list[float] | None = None,
    *,
    require_recheck: bool = True,
) -> PerformanceGate:
    before = baseline_codegen or CodegenEvidence()
    after = candidate_codegen or CodegenEvidence()
    gate = PerformanceGate(
        decision="inconclusive",
        reason="insufficient performance evidence",
        threshold_percent=threshold_percent,
        baseline_samples_ms=list(baseline),
        candidate_samples_ms=list(candidate),
        baseline_recheck_samples_ms=list(baseline_recheck or []),
        baseline_codegen_hash=before.sha256,
        candidate_codegen_hash=after.sha256,
    )
    if "inconsistent" in {before.status, after.status}:
        gate.codegen_status = "inconsistent"
        gate.reason = "generated source hash records are malformed or inconsistent; baseline retained"
        return gate
    if before.sha256 and after.sha256:
        gate.codegen_status = "unchanged" if before.sha256 == after.sha256 else "changed"
        if before.sha256 == after.sha256:
            gate.decision = "codegen_unchanged"
            gate.reason = "generated source is unchanged; timing differences do not justify adopting a source optimization"
            return gate
    else:
        gate.uncertainty.append("Generated source comparison is unavailable; acceptance uses benchmark evidence only.")
    groups = [baseline, candidate]
    if baseline_recheck is not None:
        groups.append(baseline_recheck)
    if not math.isfinite(threshold_percent) or threshold_percent < 0:
        gate.reason = "performance threshold is invalid"
        return gate
    if any(len(samples) < MINIMUM_REPEATS for samples in groups):
        gate.reason = f"at least {MINIMUM_REPEATS} independent benchmark repeats per version are required"
        return gate
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for samples in groups for value in samples):
        gate.reason = "benchmark samples must be finite positive numbers"
        return gate
    base_median = statistics.median(baseline)
    gate.median_improvement_percent = (base_median - statistics.median(candidate)) / base_median * 100.0
    if gate.median_improvement_percent <= 0 or gate.median_improvement_percent < threshold_percent:
        gate.decision = "no_improvement"
        gate.reason = "candidate median improvement did not meet the configured threshold"
        return gate
    controls = baseline + (baseline_recheck or [])
    fastest_control = min(controls)
    slowest_candidate = max(candidate)
    gate.conservative_improvement_percent = (fastest_control - slowest_candidate) / fastest_control * 100.0
    if gate.conservative_improvement_percent <= 0 or gate.conservative_improvement_percent < threshold_percent:
        gate.reason = "repeat ranges overlap or baseline recheck does not confirm the required gain; timing noise or drift cannot be excluded"
        return gate
    if require_recheck and baseline_recheck is None:
        gate.reason = "candidate requires a repeated baseline control after measurement before acceptance"
        return gate
    gate.baseline_recheck_passed = baseline_recheck is not None
    gate.decision = "accepted"
    gate.reason = (
        "strict correctness and non-overlapping repeats met the threshold, including a repeated baseline control"
        if gate.baseline_recheck_passed
        else "preliminary timing screen passed; a baseline recheck is still required before publication"
    )
    return gate
