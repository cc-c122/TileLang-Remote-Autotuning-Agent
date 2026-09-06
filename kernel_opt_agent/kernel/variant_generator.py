from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from kernel_opt_agent.sample_security import UnsafeSampleError, copy_safe_sample_contents, normalize_sample_path

from .patch_manager import save_patch
from .template_manager import TemplateManager


class VariantGenerator:
    def __init__(
        self,
        sample_path: Path,
        entry_file: str,
        search_space: dict[str, list[Any]],
        generated_dir: Path,
        patches_dir: Path,
        allowed_patterns: list[str],
    ):
        self.sample_path = sample_path.expanduser().absolute()
        try:
            self.entry_file = normalize_sample_path(entry_file, label="kernel.entry_file")
        except UnsafeSampleError as exc:
            raise ValueError(str(exc)) from exc
        self.generated_dir = generated_dir.resolve()
        self.patches_dir = patches_dir.resolve()
        self.allowed_patterns = allowed_patterns
        self.template_path = (
            self.sample_path.joinpath(*PurePosixPath(self.entry_file).parts)
            if self.sample_path.is_dir()
            else self.sample_path
        )
        if not self._allowed(self.template_path):
            raise ValueError(f"kernel entry_file is not allowed by constraints.allowed_file_patterns: {entry_file}")
        self.template = TemplateManager(self.template_path, search_space)

    def _allowed(self, path: Path) -> bool:
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in self.allowed_patterns)

    def create_trial(self, iteration: int, candidate_id: int, config: dict[str, Any]) -> dict[str, Path]:
        name = f"iter{iteration:03d}_cand{candidate_id:03d}"
        trial_dir = (self.generated_dir / name).resolve()
        trial_dir.relative_to(self.generated_dir)
        if trial_dir.exists():
            shutil.rmtree(trial_dir)
        trial_dir.mkdir(parents=True, exist_ok=True)
        try:
            copy_safe_sample_contents(self.sample_path, trial_dir)
        except UnsafeSampleError as exc:
            raise ValueError(str(exc)) from exc

        rendered = self.template.render(config)
        target_entry = trial_dir.joinpath(*PurePosixPath(self.entry_file).parts)
        target_entry.parent.mkdir(parents=True, exist_ok=True)
        target_entry.write_text(rendered, encoding="utf-8")
        kernel_copy = self.generated_dir / f"kernel_{name}.py"
        kernel_copy.write_text(rendered, encoding="utf-8")
        patch_path = self.patches_dir / f"kernel_{name}.patch"
        save_patch(self.template.template_text, rendered, patch_path, str(self.template_path), str(target_entry))
        return {"trial_dir": trial_dir, "kernel": target_entry, "kernel_copy": kernel_copy, "patch": patch_path}
