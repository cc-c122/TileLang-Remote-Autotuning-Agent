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


def _script_body(probe_name: str, param_name: str, value: Any) -> str:
    source_name = f"_kernel_opt_safe_probe_{probe_name}_{str(value).replace(' ', '_')}.py"
    if probe_name == "threads_probe":
        kernel_source = f"""
NUM_THREADS = {value!r}
assert isinstance(NUM_THREADS, int) and NUM_THREADS > 0

def probe_kernel():
    acc = 0
    for tid in range(NUM_THREADS):
        acc += tid & 7
    return acc

probe_kernel()
"""
    elif probe_name == "vector_width_probe":
        kernel_source = f"""
VECTOR_WIDTH = {value!r}
assert isinstance(VECTOR_WIDTH, int) and VECTOR_WIDTH > 0

def probe_kernel():
    data = list(range(max(VECTOR_WIDTH * 4, 1)))
    acc = 0
    for offset in range(0, len(data), VECTOR_WIDTH):
        vec = data[offset:offset + VECTOR_WIDTH]
        acc += sum(vec)
    return acc

probe_kernel()
"""
    elif probe_name == "stages_probe":
        kernel_source = f"""
NUM_STAGES = {value!r}
assert isinstance(NUM_STAGES, int) and NUM_STAGES > 0

def probe_kernel():
    acc = 1
    for stage in range(NUM_STAGES):
        acc = (acc * 17 + stage) % 104729
    return acc

probe_kernel()
"""
    else:
        kernel_source = f"""
SHARED_MEMORY_BYTES = {value!r}
assert isinstance(SHARED_MEMORY_BYTES, int) and SHARED_MEMORY_BYTES > 0

def probe_kernel():
    shared = bytearray(SHARED_MEMORY_BYTES)
    shared[0] = 1
    shared[-1] = 2
    return shared[0] + shared[-1]

probe_kernel()
"""
    return f"""
import json
import pathlib
import py_compile

source_path = pathlib.Path({source_name!r})
source_path.write_text({kernel_source!r}, encoding="utf-8")
py_compile.compile(str(source_path), doraise=True)
namespace = {{}}
exec(source_path.read_text(encoding="utf-8"), namespace, namespace)
print(json.dumps({{"param_name": {param_name!r}, "candidate_value": {value!r}, "compiled": True, "executed": True}}, sort_keys=True))
"""


def _remote_probe_command(probe_name: str, param_name: str, value: Any) -> str:
    return "python - <<'PY'\n" + _script_body(probe_name, param_name, value).strip() + "\nPY"


def _probe_command(probe_name: str, param_name: str, value: Any, runner_mode: str) -> str:
    if runner_mode == "local_mock":
        return _local_mock_command(param_name, value)
    return _remote_probe_command(probe_name, param_name, value)


def _infer_probe(probe_name: str, param_name: str, value: Any, status: str, runner_mode: str) -> tuple[str, str]:
    if runner_mode == "local_mock":
        if status == "pass":
            return f"runner_mode=local_mock; {param_name}={value} passed a mock availability probe; real GPU capability was not verified", "low"
        return f"runner_mode=local_mock; {param_name}={value} did not pass a mock availability probe; real GPU capability was not verified", "low"
    if status == "pass":
        return f"runner_mode={runner_mode}; {param_name}={value} compiled and ran a small probe script; this is not an official hardware limit", "medium"
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
            try:
                result: CommandResult = runner.run(
                    f"safe_probe_{probe.name}_{safe_value}",
                    _probe_command(probe.name, probe.param_name, value, runner_mode),
                )
                stdout = result.stdout
                stderr = result.stderr
                if result.guard_denied:
                    status = "guard_denied"
                    stderr = result.error_message or result.stderr
                elif result.timeout:
                    status = "timeout"
                elif result.returncode == 0:
                    status = "pass"
                else:
                    status = "failed"
                    stderr = result.error_message or result.stderr or f"return code {result.returncode}"
            except Exception as exc:
                status = "exception"
                stderr = str(exc)

            _write_text(stdout_path, stdout)
            _write_text(stderr_path, stderr)
            inference, confidence = _infer_probe(probe.name, probe.param_name, value, status, runner_mode)
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
