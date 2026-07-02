from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel_opt_agent.runner.local_runner import CommandResult, LocalRunner
from kernel_opt_agent.runner.ssh_runner import SSHRunner

from .hardware_info import HardwareInfo


@dataclass(frozen=True)
class SafeProbe:
    name: str
    param_name: str
    values: list[Any] | None = None


SHARED_MEMORY_CANDIDATES = [32768, 49152, 65536, 98304, 131072]
REMOTE_PASS_MARKER = "SAFE_PROBE_RESULT status=PASS"
REMOTE_SKIPPED_MARKER = "SAFE_PROBE_RESULT status=SKIPPED"


SAFE_PROBES = [
    SafeProbe("threads_probe", "NUM_THREADS"),
    SafeProbe("vector_width_probe", "VECTOR_WIDTH"),
    SafeProbe("stages_probe", "NUM_STAGES"),
    SafeProbe("shared_memory_probe", "SHARED_MEMORY_BYTES", SHARED_MEMORY_CANDIDATES),
]


def _runner_mode(runner: Any) -> str:
    explicit = getattr(runner, "probe_runner_mode", None)
    if explicit:
        return str(explicit)
    if isinstance(runner, LocalRunner):
        return "local_mock"
    if isinstance(runner, SSHRunner):
        return "ssh_probe"
    return "remote_probe"


def _local_mock_command(param_name: str, value: Any) -> str:
    return (
        "python -c \"import json; "
        f"print(json.dumps({{'param_name': {param_name!r}, 'candidate_value': {value!r}}}, sort_keys=True))\""
    )


def _hardware_backend(hardware_info: HardwareInfo | None) -> str:
    if hardware_info is None:
        return "unknown"
    backend = hardware_info.fields.get("backend")
    if backend is None or backend.value in (None, "", "unknown"):
        return "unknown"
    return str(backend.value).lower()


def _tilelang_probe_script(probe_name: str, param_name: str, value: Any, backend: str) -> str:
    num_threads = int(value) if probe_name == "threads_probe" else 128
    vector_width = int(value) if probe_name == "vector_width_probe" else 1
    num_stages = int(value) if probe_name == "stages_probe" else 2
    shared_elems = max(1, int(value) // 4) if probe_name == "shared_memory_probe" else max(vector_width, 1)
    n = max(16, vector_width * 4, min(shared_elems, 256))
    return f"""
import json
import sys

def emit(status, reason, code=0):
    print("SAFE_PROBE_RESULT status=" + status + " reason=" + json.dumps(reason))
    raise SystemExit(code)

BACKEND = {backend!r}
IS_MXMACA = BACKEND in {{"mxmaca", "metax", "metax_c500"}}

if IS_MXMACA:
    try:
        import mctilelang as tilelang
        import mctilelang.language as T
    except Exception as exc:
        emit("SKIPPED", "mcTileLang unavailable for mxmaca/metax probe: " + str(exc), 0)
else:
    try:
        import tilelang
        import tilelang.language as T
    except Exception as exc:
        try:
            import mctilelang as tilelang
            import mctilelang.language as T
        except Exception as mc_exc:
            emit("SKIPPED", "TileLang/mcTileLang compatible Python module unavailable: " + str(exc) + "; " + str(mc_exc), 0)

try:
    import torch
except Exception as exc:
    emit("SKIPPED", "torch runtime unavailable for safe probe: " + str(exc), 0)

if not hasattr(torch, "cuda") or not torch.cuda.is_available():
    if IS_MXMACA:
        emit("SKIPPED", "mxmaca/mcPyTorch runtime unavailable: torch cuda-compatible device is not available", 0)
    emit("SKIPPED", "CUDA/GPU runtime unavailable for TileLang probe", 0)

if not hasattr(tilelang, "jit"):
    emit("SKIPPED", "TileLang jit API unavailable", 0)

DEVICE = "cuda"
NUM_THREADS = {num_threads}
VECTOR_WIDTH = {vector_width}
NUM_STAGES = {num_stages}
SHARED_ELEMS = {shared_elems}
N = {n}

try:
    @tilelang.jit
    def safe_probe_kernel():
        @T.prim_func
        def kernel(A: T.Tensor((N,), "float32"), B: T.Tensor((N,), "float32")):
            with T.Kernel(1, threads=NUM_THREADS) as bx:
                shared = T.alloc_shared((SHARED_ELEMS,), "float32")
                for stage in T.Pipelined(1, num_stages=NUM_STAGES):
                    for i in T.Parallel(VECTOR_WIDTH):
                        shared[i] = A[i]
                        B[i] = shared[i]
        return kernel

    compiled = safe_probe_kernel()
    a = torch.ones((N,), device=DEVICE, dtype=torch.float32)
    b = torch.empty((N,), device=DEVICE, dtype=torch.float32)
    compiled(a, b)
    torch.cuda.synchronize()
except Exception as exc:
    emit("FAILED", "{probe_name} TileLang/GPU small kernel failed for {param_name}={value}: " + str(exc), 1)

if IS_MXMACA:
    emit("PASS", "{probe_name} mxmaca/metax small kernel compiled and ran for {param_name}={value}", 0)
emit("PASS", "{probe_name} TileLang/GPU small kernel compiled and ran for {param_name}={value}", 0)
"""


def _remote_probe_command(probe_name: str, param_name: str, value: Any, backend: str) -> str:
    return "python - <<'PY'\n" + _tilelang_probe_script(probe_name, param_name, value, backend).strip() + "\nPY"


def _probe_command(probe_name: str, param_name: str, value: Any, runner_mode: str, backend: str) -> str:
    if runner_mode == "local_mock":
        return _local_mock_command(param_name, value)
    return _remote_probe_command(probe_name, param_name, value, backend)


def _infer_probe(probe_name: str, param_name: str, value: Any, status: str, runner_mode: str, detail: str = "") -> tuple[str, str]:
    if runner_mode == "local_mock":
        if status == "pass":
            return f"runner_mode=local_mock; {param_name}={value} passed a mock availability probe; real GPU capability was not verified", "low"
        return f"runner_mode=local_mock; {param_name}={value} did not pass a mock availability probe; real GPU capability was not verified", "low"
    if status == "pass":
        if detail:
            return f"runner_mode={runner_mode}; {detail}; this is not an official hardware limit", "medium"
        return f"runner_mode={runner_mode}; {param_name}={value} compiled and ran a TileLang/GPU small kernel; this is not an official hardware limit", "medium"
    if status == "skipped":
        reason = detail or "TileLang/GPU runtime was unavailable"
        return f"runner_mode={runner_mode}; {param_name}={value} skipped: {reason}", "low"
    if status == "guard_denied":
        return f"runner_mode={runner_mode}; {param_name}={value} was not probed because command guard denied it", "low"
    if status == "timeout":
        return f"runner_mode={runner_mode}; {param_name}={value} timed out during lightweight availability probing", "low"
    if status == "exception":
        return f"runner_mode={runner_mode}; {param_name}={value} could not be probed because the runner raised an exception", "low"
    return f"runner_mode={runner_mode}; {param_name}={value} failed a lightweight availability probe", "low"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def _probe_values(probe: SafeProbe, search_space: dict[str, list[Any]]) -> list[Any]:
    if probe.values is not None:
        return list(probe.values)
    return list(search_space.get(probe.param_name, []))


def _marker_reason(stdout: str, marker: str) -> str:
    for line in stdout.splitlines():
        if marker not in line:
            continue
        _, _, raw_reason = line.partition("reason=")
        try:
            return str(json.loads(raw_reason))
        except json.JSONDecodeError:
            return raw_reason.strip()
    return ""


def _status_from_result(result: CommandResult, runner_mode: str) -> tuple[str, str, str]:
    if result.guard_denied:
        return "guard_denied", result.error_message or result.stderr, ""
    if result.timeout:
        return "timeout", result.stderr, ""
    if runner_mode == "local_mock":
        return ("pass" if result.returncode == 0 else "failed"), result.stderr, ""
    if REMOTE_PASS_MARKER in result.stdout and result.returncode == 0:
        return "pass", result.stderr, _marker_reason(result.stdout, REMOTE_PASS_MARKER)
    if REMOTE_SKIPPED_MARKER in result.stdout:
        reason = _marker_reason(result.stdout, REMOTE_SKIPPED_MARKER)
        return "skipped", result.stderr or reason, reason
    if result.returncode == 0:
        return "failed", result.stderr or "remote TileLang probe did not emit SAFE_PROBE_RESULT status=PASS", ""
    return "failed", result.error_message or result.stderr or f"return code {result.returncode}", ""


def run_safe_probes(
    runner: Any,
    results_dir: Path,
    search_space: dict[str, list[Any]],
    timeout_seconds: int,
    hardware_info: HardwareInfo | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_dir = results_dir / "logs" / "hardware_probe"
    jsonl_path = results_dir / "hardware_probe.jsonl"
    records: list[dict[str, Any]] = []
    log_lines: list[str] = []
    runner_mode = _runner_mode(runner)
    backend = _hardware_backend(hardware_info)

    for probe in SAFE_PROBES:
        values = _probe_values(probe, search_space)
        if not values:
            log_lines.append(f"safe probe {probe.name}: skipped; {probe.param_name} not in search_space")
            continue
        for value in values:
            safe_value = str(value).replace("/", "_").replace("\\", "_").replace(" ", "_")
            log_name = f"{probe.name}_{safe_value}"
            stdout_path = log_dir / f"{log_name}.stdout.log"
            stderr_path = log_dir / f"{log_name}.stderr.log"
            status = "failed"
            stdout = ""
            stderr = ""
            detail = ""
            try:
                result: CommandResult = runner.run(
                    f"safe_probe_{probe.name}_{safe_value}",
                    _probe_command(probe.name, probe.param_name, value, runner_mode, backend),
                )
                stdout = result.stdout
                stderr = result.stderr
                status, stderr, detail = _status_from_result(result, runner_mode)
            except Exception as exc:
                status = "exception"
                stderr = str(exc)

            _write_text(stdout_path, stdout)
            _write_text(stderr_path, stderr)
            inference, confidence = _infer_probe(probe.name, probe.param_name, value, status, runner_mode, detail)
            record = {
                "probe_name": probe.name,
                "param_name": probe.param_name,
                "candidate_value": value,
                "status": status,
                "inference": inference,
                "source": "safe_probe",
                "confidence": confidence,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
            }
            records.append(record)
            log_lines.append(f"safe probe {probe.name} {probe.param_name}={value}: {status}")
            if probe.name == "shared_memory_probe" and status != "pass":
                log_lines.append(f"safe probe {probe.name}: stopping after first failed shared-memory candidate")
                break

    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    return records, log_lines
