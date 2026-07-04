from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .patch_region import replace_region
from .patch_validator import PatchProposal, validate_patch_proposal
from .rollback import RollbackRecord, create_backup


@dataclass(frozen=True)
class AppliedPatch:
    target_path: str
    target_region: str
    optimization_name: str
    rollback: RollbackRecord

    def to_dict(self) -> dict[str, object]:
        return {
            "target_path": self.target_path,
            "target_region": self.target_region,
            "optimization_name": self.optimization_name,
            "rollback": self.rollback.to_dict(),
        }


def apply_validated_patch(
    target_path: Path,
    proposal: PatchProposal,
    backup_dir: Path,
    allowed_regions: set[str] | None = None,
) -> AppliedPatch:
    target = target_path.resolve()
    source_text = target.read_text(encoding="utf-8")
    validation = validate_patch_proposal(source_text, proposal, allowed_regions)
    if not validation.ok:
        raise ValueError("; ".join(validation.errors))
    rollback = create_backup(target, backup_dir)
    patched = replace_region(source_text, proposal.target_region, proposal.replacement)
    target.write_text(patched, encoding="utf-8")
    return AppliedPatch(str(target), proposal.target_region, proposal.optimization_name, rollback)
