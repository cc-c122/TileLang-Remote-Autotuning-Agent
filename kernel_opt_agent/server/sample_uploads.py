from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import Request
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from kernel_opt_agent.sample_security import (
    UnsafeSampleError,
    ensure_no_link_components,
    file_contains_private_key,
    is_link_or_reparse,
    normalize_sample_path as normalize_safe_sample_path,
    private_key_marker_in_bytes,
    validate_sample_file_paths,
)


MAX_UPLOAD_FILES = 100
MAX_UPLOAD_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_REQUEST_OVERHEAD_BYTES = 512 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_TOTAL_BYTES + MAX_UPLOAD_REQUEST_OVERHEAD_BYTES
READ_CHUNK_BYTES = 64 * 1024

_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SampleUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def normalize_sample_path(raw_path: str, *, label: str = "filename") -> str:
    try:
        return normalize_safe_sample_path(raw_path, label=label)
    except UnsafeSampleError as exc:
        raise SampleUploadError(str(exc)) from exc


def _normalize_upload_paths(raw_paths: list[str]) -> list[str]:
    try:
        return validate_sample_file_paths(raw_paths)
    except UnsafeSampleError as exc:
        raise SampleUploadError(str(exc)) from exc


def _ensure_no_upload_links(path: Path) -> None:
    try:
        ensure_no_link_components(path, label="sample upload path")
    except UnsafeSampleError as exc:
        raise SampleUploadError(str(exc)) from exc


class _UploadLimitExceeded(MultiPartException):
    pass


class _BoundedMultiPartParser(MultiPartParser):
    def __init__(self, headers: Any, stream: AsyncGenerator[bytes, None]):
        super().__init__(
            headers,
            stream,
            max_files=MAX_UPLOAD_FILES,
            max_fields=1,
        )
        self._current_file_bytes = 0
        self._current_field_bytes = 0
        self._total_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0
        self._current_field_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            size = end - start
            self._current_file_bytes += size
            self._total_file_bytes += size
            if self._current_file_bytes > MAX_UPLOAD_FILE_BYTES:
                raise _UploadLimitExceeded(f"file exceeds {MAX_UPLOAD_FILE_BYTES} byte limit")
            if self._total_file_bytes > MAX_UPLOAD_TOTAL_BYTES:
                raise _UploadLimitExceeded(f"sample exceeds {MAX_UPLOAD_TOTAL_BYTES} byte total limit")
        else:
            self._current_field_bytes += end - start
            if self._current_field_bytes > 4096:
                raise _UploadLimitExceeded("multipart metadata field exceeds 4096 byte limit")
        super().on_part_data(data, start, end)

    async def parse(self) -> FormData:
        try:
            return await super().parse()
        except Exception:
            for file in self._files_to_close_on_error:
                file.close()
            raise


async def parse_sample_upload_request(request: Request) -> tuple[list[UploadFile], str]:
    raw_length = request.headers.get("content-length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = None
    if content_length is not None and content_length > MAX_UPLOAD_REQUEST_BYTES:
        raise SampleUploadError(
            f"multipart request exceeds {MAX_UPLOAD_REQUEST_BYTES} byte limit",
            status_code=413,
        )

    received_bytes = 0

    async def bounded_stream() -> AsyncGenerator[bytes, None]:
        nonlocal received_bytes
        async for chunk in request.stream():
            received_bytes += len(chunk)
            if received_bytes > MAX_UPLOAD_REQUEST_BYTES:
                raise _UploadLimitExceeded(
                    f"multipart request exceeds {MAX_UPLOAD_REQUEST_BYTES} byte limit"
                )
            yield chunk

    parser = _BoundedMultiPartParser(request.headers, bounded_stream())
    try:
        form = await parser.parse()
    except _UploadLimitExceeded as exc:
        raise SampleUploadError(exc.message, status_code=413) from exc
    except MultiPartException as exc:
        limit_error = exc.message.startswith(("Too many files.", "Too many fields."))
        raise SampleUploadError(
            f"invalid multipart upload: {exc.message}",
            status_code=413 if limit_error else 422,
        ) from exc
    except Exception as exc:
        raise SampleUploadError("invalid multipart upload") from exc

    uploads: list[UploadFile] = []
    entry_values: list[str] = []
    try:
        for field_name, value in form.multi_items():
            if field_name == "files" and isinstance(value, UploadFile):
                uploads.append(value)
            elif field_name == "entry_file" and isinstance(value, str):
                entry_values.append(value)
            else:
                raise SampleUploadError(f"unexpected multipart field: {field_name}")
        if len(entry_values) != 1:
            raise SampleUploadError("exactly one entry_file field is required")
        if not uploads:
            raise SampleUploadError("at least one files field is required")
        return uploads, entry_values[0]
    except Exception:
        await form.close()
        raise


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
        normalized_paths = _normalize_upload_paths([upload.filename or "" for upload in uploads])
        upload_id = uuid.uuid4().hex
        stage = self.root / f".staging-{upload_id}"
        destination = self.root / upload_id
        _assert_within(stage, self.root)
        _assert_within(destination, self.root)
        sample_root = stage / "sample"
        records: list[dict[str, Any]] = []
        uploaded_paths = set(normalized_paths)
        total_size = 0

        try:
            sample_root.mkdir(parents=True, exist_ok=False)
            for upload, relative_path in zip(uploads, normalized_paths, strict=True):
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
                        found_marker, marker_window = private_key_marker_in_bytes(chunk, marker_window)
                        if found_marker:
                            raise SampleUploadError(f"private key content is not allowed: {relative_path}")
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
        _ensure_no_upload_links(upload_root)
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
        raw_paths: list[str] = []
        for record in files:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise SampleUploadError("sample upload manifest has an invalid file record")
            raw_paths.append(record["path"])
        normalized_paths = _normalize_upload_paths(raw_paths)
        if normalized_paths != raw_paths:
            raise SampleUploadError("sample upload manifest contains a non-canonical path")
        total_size = 0
        for record, relative_path in zip(files, normalized_paths, strict=True):
            size = record.get("size_bytes")
            digest = record.get("sha256")
            if not isinstance(size, int) or size < 0 or size > MAX_UPLOAD_FILE_BYTES:
                raise SampleUploadError(f"sample upload manifest has an invalid size: {relative_path}")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise SampleUploadError(f"sample upload manifest has an invalid SHA-256: {relative_path}")
            total_size += size
        if total_size > MAX_UPLOAD_TOTAL_BYTES:
            raise SampleUploadError("sample upload manifest exceeds the total size limit")
        try:
            manifest_entry = normalize_sample_path(str(manifest.get("entry_file") or ""), label="entry_file")
        except SampleUploadError as exc:
            raise SampleUploadError("sample upload manifest entry_file is invalid") from exc
        if manifest_entry != manifest.get("entry_file") or manifest_entry not in set(normalized_paths):
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
        _ensure_no_upload_links(source_root)
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
                _ensure_no_upload_links(source)
                if is_link_or_reparse(source) or not source.is_file():
                    raise SampleUploadError(f"uploaded artifact is not a regular file: {relative_path}")
                if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
                    raise SampleUploadError(f"uploaded artifact is not a regular file: {relative_path}")
                if source.stat().st_size != record.get("size_bytes") or _sha256_file(source) != record.get("sha256"):
                    raise SampleUploadError(f"uploaded artifact failed integrity validation: {relative_path}")
                if file_contains_private_key(source):
                    raise SampleUploadError(f"uploaded artifact contains private key material: {relative_path}")
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
