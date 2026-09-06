from __future__ import annotations

from .patch_generator import AppliedPatch, apply_validated_patch
from .patch_region import PatchRegion, find_patch_regions, replace_region
from .patch_runner import run_patch_trial
from .patch_trial import PATCH_TRIAL_STATUSES, PatchTrial, write_patch_trials_jsonl
from .patch_validator import PatchProposal, PatchValidationResult, validate_patch_proposal
from .rollback import RollbackRecord, create_backup, rollback_file

__all__ = [
    "AppliedPatch",
    "PATCH_TRIAL_STATUSES",
    "PatchProposal",
    "PatchRegion",
    "PatchTrial",
    "PatchValidationResult",
    "RollbackRecord",
    "apply_validated_patch",
    "create_backup",
    "find_patch_regions",
    "replace_region",
    "rollback_file",
    "run_patch_trial",
    "validate_patch_proposal",
    "write_patch_trials_jsonl",
]
