from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from kernel_opt_agent.sample_security import normalize_sample_path


MAX_GENERATED_SOURCE_BYTES = 2 * 1024 * 1024
_DIGEST_RECORD = re.compile(r"^GENERATED_SOURCE_SHA256=([0-9a-f]{64})[ \t]*\r?$", re.MULTILINE)
_C_TEXT = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_KERNEL = re.compile(
    r"\b__global__\s+void\s+(?:__launch_bounds__\s*\([^()]*\)\s*)?"
    r"([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
)
_MATRIX_CALL = re.compile(
    r"\b__builtin_mxc_mma_[A-Za-z0-9_]+\s*\("
    r"|\btl::gemm(?:_(?:ss|rs|sr|rr))?\s*(?:<|\()"
    r"|\bnvcuda::wmma::mma_sync\s*(?:<|\()"
)
SELECTION_WARNING = (
    "automatic target selection is a generated-code structural preference, "
    "not proof of counter attribution; attribution remains unverified"
)


def generated_kernel_symbols(source: str) -> tuple[list[str], list[str]]:
    # Ignore prototypes and literal/comment text; unsupported declarations fail
    # closed rather than selecting a kernel by report order or measured counters.
    cleaned = _C_TEXT.sub(lambda match: "\n" * match.group().count("\n") + " ", source)
    kernels: dict[str, bool] = {}
    for match in _KERNEL.finditer(cleaned):
        depth, cursor = 1, match.end()
        start = cursor
        while cursor < len(cleaned) and depth:
            depth += (cleaned[cursor] == "{") - (cleaned[cursor] == "}")
            cursor += 1
        if depth or match.group(1) in kernels:
            return [], []
        kernels[match.group(1)] = bool(_MATRIX_CALL.search(cleaned[start:cursor - 1]))
    return sorted(kernels), sorted(name for name, matrix in kernels.items() if matrix)


def _generated_artifact(build_log: str) -> tuple[str, str] | None:
    digests = set(_DIGEST_RECORD.findall(build_log))
    records = set()
    for line in build_log.splitlines():
        _, marker, payload = line.partition("{")
        if not marker:
            continue
        try:
            row = json.loads(marker + payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        path, digest = row.get("generated_source_path"), row.get("generated_source_sha256")
        if isinstance(path, str) and isinstance(digest, str):
            records.add((path, digest))
    if not records:
        return None
    if len(records) != 1 or len(digests) != 1:
        raise ValueError("generated source records are ambiguous")
    path, digest = next(iter(records))
    if digest not in digests:
        raise ValueError("generated source records disagree")
    normalized = normalize_sample_path(path, label="generated source")
    if normalized != path or Path(path).suffix not in {".cc", ".cpp", ".cu", ".maca"}:
        raise ValueError("generated source path is not a relative compiler artifact")
    if any(part.startswith(".") for part in Path(path).parts):
        raise ValueError("generated source path uses a hidden directory")
    return path, digest


def resolve_kernel_target(
    runner: Any,
    results_dir: Path,
    collection_id: str,
    build_log: str | None,
    explicit_names: tuple[str, ...],
    should_cancel: Callable[[], bool] | None = None,
    secret_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    result = {"mode": "explicit" if explicit_names else "automatic", "status": "unresolved",
              "reason": "no_generated_source_record", "selected_kernel_names": list(explicit_names),
              "compiled_kernel_names": [], "matrix_kernel_names": [], "warnings": []}
    if explicit_names:
        result.update(status="selected", reason="explicit_kernel_names")
        return result
    if should_cancel and should_cancel():
        result.update(status="cancelled", reason="cancelled")
        return result
    try:
        artifact = _generated_artifact(build_log or "")
    except ValueError:
        result.update(status="invalid", reason="invalid_generated_source_record")
        return result
    if artifact is None:
        return result
    relative, digest = artifact
    destination = results_dir / "mcprofiler_targets" / collection_id / "generated_source.cc"
    destination.resolve().relative_to(results_dir.resolve())
    if destination.exists():
        result.update(status="invalid", reason="target_artifact_already_exists")
        return result
    try:
        runner.download(relative, destination, max_files=1, max_bytes=MAX_GENERATED_SOURCE_BYTES)
        content = destination.read_bytes()
        if len(content) > MAX_GENERATED_SOURCE_BYTES:
            raise ValueError("generated source exceeds size limit")
        if any(value and value.encode() in content for value in secret_values):
            raise ValueError("generated source contains a configured secret")
        if hashlib.sha256(content).hexdigest() != digest or runner.file_sha256(relative) != digest:
            raise ValueError("generated source hash mismatch")
        kernels, matrix = generated_kernel_symbols(content.decode("utf-8"))
    except Exception as exc:
        if destination.is_file():
            destination.resolve().relative_to(results_dir.resolve())
            destination.unlink()
        result.update(status="invalid", reason="generated_source_unverified", exception_type=type(exc).__name__)
        return result
    result.update(generated_source_sha256=digest, generated_source_path=relative,
                  generated_source_artifact=destination.relative_to(results_dir).as_posix(),
                  compiled_kernel_names=kernels, matrix_kernel_names=matrix, warnings=[SELECTION_WARNING])
    if should_cancel and should_cancel():
        result.update(status="cancelled", reason="cancelled")
    elif len(kernels) == 1:
        result.update(status="selected", reason="single_compiled_kernel", selected_kernel_names=kernels)
    elif len(kernels) > 1 and len(matrix) == 1:
        result.update(status="selected", reason="unique_matrix_intrinsic_kernel", selected_kernel_names=matrix)
    else:
        result.update(status="invalid", reason="ambiguous_compiled_kernels" if kernels else "compiled_kernel_symbols_unavailable")
    return result
