from __future__ import annotations

from .patch_generator import AppliedPatch, apply_validated_patch
from .patch_region import PatchRegion, find_patch_regions, replace_region
from .patch_validator import PatchProposal, PatchValidationResult, validate_patch_proposal
from .rollback import RollbackRecord, create_backup, rollback_file

__all__ = [
    "AppliedPatch",
    "PatchProposal",
    "PatchRegion",
    "PatchValidationResult",
    "RollbackRecord",
    "apply_validated_patch",
    "create_backup",
    "find_patch_regions",
    "replace_region",
    "rollback_file",
    "validate_patch_proposal",
]
