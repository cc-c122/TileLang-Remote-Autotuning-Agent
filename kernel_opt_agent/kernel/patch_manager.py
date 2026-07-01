from __future__ import annotations

import difflib
from pathlib import Path


def save_patch(original_text: str, rendered_text: str, patch_path: Path, fromfile: str, tofile: str) -> Path:
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    diff = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        rendered_text.splitlines(keepends=True),
        fromfile=fromfile,
        tofile=tofile,
    )
    patch_path.write_text("".join(diff), encoding="utf-8")
    return patch_path

