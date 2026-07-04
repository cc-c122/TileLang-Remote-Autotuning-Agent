from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .patch_region import find_patch_regions


FORBIDDEN_PATCH_TOKENS = (
    "BEGIN_AGENT_PATCH",
    "END_AGENT_PATCH",
    "subprocess.",
    "os.system",
    "Popen(",
    "shell=True",
)


@dataclass(frozen=True)
class PatchProposal:
    optimization_name: str
    target_region: str
    hypothesis: str
    expected_improvement: str
    risk: str
    replacement: str

    def to_dict(self) -> dict[str, str]:
        return {
            "optimization_name": self.optimization_name,
            "target_region": self.target_region,
            "hypothesis": self.hypothesis,
            "expected_improvement": self.expected_improvement,
            "risk": self.risk,
            "replacement": self.replacement,
        }


@dataclass(frozen=True)
class PatchValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    region_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "region_names": self.region_names,
        }


def validate_patch_proposal(
    source_text: str,
    proposal: PatchProposal,
    allowed_regions: set[str] | None = None,
) -> PatchValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    regions = find_patch_regions(source_text)
    region_names = [region.name for region in regions]
    if allowed_regions is not None and proposal.target_region not in allowed_regions:
        errors.append(f"target_region is not allowed: {proposal.target_region}")
    if proposal.target_region not in region_names:
        errors.append(f"target_region is not present in source: {proposal.target_region}")
    if not proposal.optimization_name:
        errors.append("optimization_name is required")
    if not proposal.hypothesis:
        warnings.append("hypothesis is empty")
    if any(token in proposal.replacement for token in FORBIDDEN_PATCH_TOKENS):
        errors.append("replacement contains forbidden patch token or shell execution pattern")
    return PatchValidationResult(ok=not errors, errors=errors, warnings=warnings, region_names=region_names)
