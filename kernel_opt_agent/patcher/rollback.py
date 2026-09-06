from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RollbackRecord:
    target_path: str
    backup_path: str
    original_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "target_path": self.target_path,
            "backup_path": self.backup_path,
            "original_sha256": self.original_sha256,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_backup(target_path: Path, backup_dir: Path) -> RollbackRecord:
    target = target_path.resolve()
    if not target.exists() or not target.is_file():
        raise ValueError(f"target file does not exist: {target_path}")
    backup_root = backup_dir.resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{target.name}.{_sha256(target)[:12]}.bak"
    shutil.copy2(target, backup_path)
    return RollbackRecord(str(target), str(backup_path), _sha256(target))


def rollback_file(record: RollbackRecord) -> None:
    target = Path(record.target_path)
    backup = Path(record.backup_path)
    if not backup.exists() or not backup.is_file():
        raise ValueError(f"backup file does not exist: {backup}")
    shutil.copy2(backup, target)
    restored_sha = _sha256(target)
    if restored_sha != record.original_sha256:
        raise ValueError("rollback verification failed: restored file hash mismatch")
