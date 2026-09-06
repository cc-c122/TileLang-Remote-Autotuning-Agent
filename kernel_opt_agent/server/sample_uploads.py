from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import UploadFile


MAX_UPLOAD_FILES = 100
MAX_UPLOAD_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 10 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"PUTTY-USER-KEY-FILE-",
)
_SENSITIVE_COMPONENTS = {".git", ".ssh", ".env"}
_SENSITIVE_FILENAMES = {
    ".env",
    ".envrc",
    "authorized_keys",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx", ".ppk"}
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class SampleUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def normalize_sample_path(raw_path: str, *, label: str = "filename") -> str:
    value = raw_path or ""
    if not value:
        raise SampleUploadError(f"{label} must be a non-empty POSIX relative path")
    if value != value.strip():
        raise SampleUploadError(f"{label} must not start or end with whitespace")
    if "\x00" in value:
        raise SampleUploadError(f"{label} contains a NUL byte")
    if "\\" in value:
        raise SampleUploadError(f"{label} must use POSIX '/' separators only")
    if value.startswith(("/", "//")) or _WINDOWS_DRIVE_RE.match(value):
        raise SampleUploadError(f"{label} must be relative to the sample root")

    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SampleUploadError(f"{label} contains an unsafe path component")
    if any(":" in part or part.endswith((".", " ")) for part in raw_parts):
        raise SampleUploadError(f"{label} contains a platform-unsafe path component")
    if any(part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES for part in raw_parts):
        raise SampleUploadError(f"{label} contains a reserved path component")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise SampleUploadError(f"{label} must stay inside the sample root")

    lowered_parts = [part.casefold() for part in path.parts]
    if any(part in _SENSITIVE_COMPONENTS or part.startswith(".env.") for part in lowered_parts):
        raise SampleUploadError(f"{label} refers to a sensitive directory")
    basename = lowered_parts[-1]
    if basename in _SENSITIVE_FILENAMES or basename.startswith(".env."):
        raise SampleUploadError(f"{label} refers to a sensitive file")
    if any(basename.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
        raise SampleUploadError(f"{label} appears to contain private key material")
    return path.as_posix()


def _assert_within(child: Path, parent: Path) -> None:
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    if child_resolved != parent_resolved and parent_resolved not in child_resolved.parents:
        raise SampleUploadError("upload path escaped the managed upload root")


def _remove_managed_tree(path: Path, parent: Path) -> None:
    _assert_within(path, parent)
    if path.exists():
        shutil.rmtree(path)


def _ensure_regular_upload(upload: UploadFile) -> None:
    try:
        mode = os.fstat(upload.file.fileno()).st_mode
    except AttributeError:
        return
    except OSError as exc:
        raise SampleUploadError("upload stream type could not be verified") from exc
    if not stat.S_ISREG(mode):
        raise SampleUploadError("only regular files can be uploaded")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SampleUploadStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def create(self, uploads: list[UploadFile], entry_file: str) -> dict[str, Any]:
        if not uploads:
            raise SampleUploadError("at least one files field is required")
        if len(uploads) > MAX_UPLOAD_FILES:
            raise SampleUploadError(f"sample contains more than {MAX_UPLOAD_FILES} files", status_code=413)

        normalized_entry = normalize_sample_path(entry_file, label="entry_file")
        upload_id = uuid.uuid4().hex
        stage = self.root / f".staging-{upload_id}"
        destination = self.root / upload_id
        _assert_within(stage, self.root)
        _assert_within(destination, self.root)
        sample_root = stage / "sample"
        records: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        uploaded_paths: set[str] = set()
        total_size = 0

        try:
            sample_root.mkdir(parents=True, exist_ok=False)
            for upload in uploads:
                relative_path = normalize_sample_path(upload.filename or "")
                path_key = relative_path.casefold()
                if path_key in seen_paths:
                    raise SampleUploadError(f"duplicate upload path: {relative_path}")
                seen_paths.add(path_key)
                uploaded_paths.add(relative_path)
                _ensure_regular_upload(upload)

                target = sample_root.joinpath(*PurePosixPath(relative_path).parts)
                _assert_within(target, sample_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                marker_window = b""
                with target.open("xb") as output:
                    while True:
                        chunk = await upload.read(READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        size += len(chunk)
                        total_size += len(chunk)
                        if size > MAX_UPLOAD_FILE_BYTES:
                            raise SampleUploadError(
                                f"file exceeds {MAX_UPLOAD_FILE_BYTES} byte limit: {relative_path}",
                                status_code=413,
                            )
                        if total_size > MAX_UPLOAD_TOTAL_BYTES:
                            raise SampleUploadError(
                                f"sample exceeds {MAX_UPLOAD_TOTAL_BYTES} byte total limit",
                                status_code=413,
                            )
                        scan_window = marker_window + chunk.upper()
                        if any(marker in scan_window for marker in _PRIVATE_KEY_MARKERS):
                            raise SampleUploadError(f"private key content is not allowed: {relative_path}")
                        marker_window = scan_window[-max(map(len, _PRIVATE_KEY_MARKERS)) :]
                        digest.update(chunk)
                        output.write(chunk)
                records.append({"path": relative_path, "size_bytes": size, "sha256": digest.hexdigest()})

            if normalized_entry not in uploaded_paths:
                raise SampleUploadError("entry_file must name one of the uploaded files")

            manifest = {
                "schema_version": "v2.sample_upload.v1",
                "upload_id": upload_id,
                "entry_file": normalized_entry,
                "files": records,
                "limits": {
                    "max_files": MAX_UPLOAD_FILES,
                    "max_file_bytes": MAX_UPLOAD_FILE_BYTES,
                    "max_total_bytes": MAX_UPLOAD_TOTAL_BYTES,
                },
            }
            (stage / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            stage.rename(destination)
            return manifest
        except Exception:
            _remove_managed_tree(stage, self.root)
            raise

    def load_manifest(self, upload_id: str) -> dict[str, Any]:
        normalized = (upload_id or "").strip().lower()
        if not _UPLOAD_ID_RE.fullmatch(normalized):
            raise SampleUploadError("unknown sample.upload_id")
        upload_root = self.root / normalized
        _assert_within(upload_root, self.root)
        manifest_path = upload_root / "manifest.json"
        if not manifest_path.is_file():
            raise SampleUploadError("unknown sample.upload_id")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SampleUploadError("sample upload manifest is unavailable") from exc
        if manifest.get("upload_id") != normalized or manifest.get("schema_version") != "v2.sample_upload.v1":
            raise SampleUploadError("sample upload manifest is invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or not files or len(files) > MAX_UPLOAD_FILES:
            raise SampleUploadError("sample upload manifest has an invalid file list")
        seen_paths: set[str] = set()
        total_size = 0
        for record in files:
            if not isinstance(record, dict):
                raise SampleUploadError("sample upload manifest has an invalid file record")
            relative_path = normalize_sample_path(str(record.get("path") or ""))
            path_key = relative_path.casefold()
            if path_key in seen_paths:
                raise SampleUploadError(f"duplicate path in upload manifest: {relative_path}")
            seen_paths.add(path_key)
            size = record.get("size_bytes")
            digest = record.get("sha256")
            if not isinstance(size, int) or size < 0 or size > MAX_UPLOAD_FILE_BYTES:
                raise SampleUploadError(f"sample upload manifest has an invalid size: {relative_path}")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SampleUploadError(f"sample upload manifest has an invalid SHA-256: {relative_path}")
            total_size += size
        if total_size > MAX_UPLOAD_TOTAL_BYTES:
            raise SampleUploadError("sample upload manifest exceeds the total size limit")
        if manifest.get("entry_file") not in {record["path"] for record in files}:
            raise SampleUploadError("sample upload manifest entry_file is missing")
        return manifest

    def validate_reference(self, upload_id: str, entry_file: str) -> dict[str, Any]:
        manifest = self.load_manifest(upload_id)
        normalized_entry = normalize_sample_path(entry_file, label="sample.entry_file")
        if normalized_entry != manifest.get("entry_file"):
            raise SampleUploadError("sample.entry_file does not match the uploaded entry_file")
        return manifest

    def materialize(self, upload_id: str, entry_file: str, destination: Path) -> Path:
        manifest = self.validate_reference(upload_id, entry_file)
        upload_root = self.root / manifest["upload_id"]
        source_root = upload_root / "sample"
        stage = destination.parent / f".{destination.name}-staging-{uuid.uuid4().hex}"
        audit_path = destination.parent / "sample_manifest.json"
        audit_stage = destination.parent / f".sample-manifest-{uuid.uuid4().hex}.tmp"
        _assert_within(stage, destination.parent)
        _assert_within(destination, destination.parent)
        _assert_within(audit_path, destination.parent)
        _assert_within(audit_stage, destination.parent)
        seen_paths: set[str] = set()
        materialized = False
        try:
            stage.mkdir(parents=True, exist_ok=False)
            for record in manifest.get("files") or []:
                relative_path = normalize_sample_path(str(record.get("path") or ""))
                path_key = relative_path.casefold()
                if path_key in seen_paths:
                    raise SampleUploadError(f"duplicate path in upload manifest: {relative_path}")
                seen_paths.add(path_key)
                source = source_root.joinpath(*PurePosixPath(relative_path).parts)
                _assert_within(source, source_root)
                if source.is_symlink() or not source.is_file():
                    raise SampleUploadError(f"uploaded artifact is not a regular file: {relative_path}")
                if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
                    raise SampleUploadError(f"uploaded artifact is not a regular file: {relative_path}")
                if source.stat().st_size != record.get("size_bytes") or _sha256_file(source) != record.get("sha256"):
                    raise SampleUploadError(f"uploaded artifact failed integrity validation: {relative_path}")
                target = stage.joinpath(*PurePosixPath(relative_path).parts)
                _assert_within(target, stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if destination.exists():
                raise SampleUploadError("task sample directory already exists")
            audit_stage.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            stage.rename(destination)
            materialized = True
            audit_stage.replace(audit_path)
            return destination
        except Exception:
            _remove_managed_tree(stage, destination.parent)
            if materialized:
                _remove_managed_tree(destination, destination.parent)
            if audit_stage.exists():
                audit_stage.unlink()
            raise
