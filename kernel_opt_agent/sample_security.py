from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath


READ_CHUNK_BYTES = 64 * 1024
MAX_SAMPLE_PATH_BYTES = 1024
MAX_SAMPLE_COMPONENT_BYTES = 255

PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"PUTTY-USER-KEY-FILE-",
)
PRIVATE_KEY_MARKER_WINDOW = max(map(len, PRIVATE_KEY_MARKERS))

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_INVALID_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
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


class UnsafeSampleError(ValueError):
    pass


def normalize_sample_path(raw_path: str, *, label: str = "filename") -> str:
    value = raw_path or ""
    if not value:
        raise UnsafeSampleError(f"{label} must be a non-empty POSIX relative path")
    if value != value.strip():
        raise UnsafeSampleError(f"{label} must not start or end with whitespace")
    if "\\" in value:
        raise UnsafeSampleError(f"{label} must use POSIX '/' separators only")
    if value.startswith(("/", "//")) or _WINDOWS_DRIVE_RE.match(value):
        raise UnsafeSampleError(f"{label} must be relative to the sample root")
    if len(value.encode("utf-8")) > MAX_SAMPLE_PATH_BYTES:
        raise UnsafeSampleError(f"{label} is too long")

    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise UnsafeSampleError(f"{label} contains an unsafe path component")
    if any(
        _WINDOWS_INVALID_RE.search(part)
        or part.endswith((".", " "))
        or len(part.encode("utf-8")) > MAX_SAMPLE_COMPONENT_BYTES
        for part in raw_parts
    ):
        raise UnsafeSampleError(f"{label} contains a platform-unsafe path component")
    if any(part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES for part in raw_parts):
        raise UnsafeSampleError(f"{label} contains a reserved path component")

    path = PurePosixPath(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise UnsafeSampleError(f"{label} must stay inside the sample root")

    lowered_parts = [part.casefold() for part in path.parts]
    if any(part in _SENSITIVE_COMPONENTS or part.startswith(".env.") for part in lowered_parts):
        raise UnsafeSampleError(f"{label} refers to a sensitive path")
    basename = lowered_parts[-1]
    if basename in _SENSITIVE_FILENAMES:
        raise UnsafeSampleError(f"{label} refers to a sensitive file")
    if any(basename.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
        raise UnsafeSampleError(f"{label} appears to contain private key material")
    return path.as_posix()


def validate_sample_file_paths(raw_paths: list[str], *, label: str = "filename") -> list[str]:
    normalized = [normalize_sample_path(path, label=label) for path in raw_paths]
    keys: dict[tuple[str, ...], str] = {}
    for path in normalized:
        key = tuple(part.casefold() for part in PurePosixPath(path).parts)
        if key in keys:
            raise UnsafeSampleError(f"duplicate sample path: {path}")
        keys[key] = path
    for key, path in keys.items():
        for depth in range(1, len(key)):
            parent = key[:depth]
            if parent in keys:
                raise UnsafeSampleError(f"sample path conflicts with file path: {keys[parent]} and {path}")
    return normalized


def private_key_marker_in_bytes(data: bytes, prefix: bytes = b"") -> tuple[bool, bytes]:
    scan_window = prefix + data.upper()
    found = any(marker in scan_window for marker in PRIVATE_KEY_MARKERS)
    return found, scan_window[-PRIVATE_KEY_MARKER_WINDOW:]


def file_contains_private_key(path: Path) -> bool:
    marker_window = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK_BYTES), b""):
            found, marker_window = private_key_marker_in_bytes(chunk, marker_window)
            if found:
                return True
    return False


def is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def ensure_no_link_components(path: Path, *, label: str = "sample path") -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if not current.exists() and not current.is_symlink():
            break
        if is_link_or_reparse(current):
            raise UnsafeSampleError(f"{label} contains a link or reparse point: {current.name}")


def _collect_safe_files(source: Path) -> list[tuple[Path, str]]:
    ensure_no_link_components(source)
    if is_link_or_reparse(source):
        raise UnsafeSampleError(f"sample path is a link or reparse point: {source.name}")
    if source.is_file():
        relative_path = normalize_sample_path(source.name)
        if not stat.S_ISREG(source.lstat().st_mode):
            raise UnsafeSampleError(f"sample contains a special file: {relative_path}")
        if file_contains_private_key(source):
            raise UnsafeSampleError(f"sample contains private key material: {relative_path}")
        return [(source, relative_path)]
    if not source.is_dir():
        raise UnsafeSampleError(f"sample path is not a regular file or directory: {source}")

    files: list[tuple[Path, str]] = []
    seen_entries: dict[tuple[str, ...], str] = {}
    stack: list[tuple[Path, PurePosixPath]] = [(source, PurePosixPath())]
    while stack:
        directory, relative_directory = stack.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            relative = relative_directory / entry.name
            relative_path = normalize_sample_path(relative.as_posix())
            key = tuple(part.casefold() for part in relative.parts)
            if key in seen_entries:
                raise UnsafeSampleError(
                    f"sample contains case-conflicting paths: {seen_entries[key]} and {relative_path}"
                )
            seen_entries[key] = relative_path
            child = Path(entry.path)
            if is_link_or_reparse(child):
                raise UnsafeSampleError(f"sample contains a link or reparse point: {relative_path}")
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                stack.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if file_contains_private_key(child):
                    raise UnsafeSampleError(f"sample contains private key material: {relative_path}")
                files.append((child, relative_path))
            else:
                raise UnsafeSampleError(f"sample contains a special file: {relative_path}")
    return sorted(files, key=lambda item: item[1].casefold())


def copy_safe_sample_contents(source: Path, destination: Path) -> list[str]:
    files = _collect_safe_files(source)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source_file, relative_path in files:
        if is_link_or_reparse(source_file) or not stat.S_ISREG(source_file.lstat().st_mode):
            raise UnsafeSampleError(f"sample changed during copy: {relative_path}")
        target = destination.joinpath(*PurePosixPath(relative_path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
        copied.append(relative_path)
    return copied
