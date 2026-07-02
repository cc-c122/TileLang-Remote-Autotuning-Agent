from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel_opt_agent.runner.local_runner import CommandResult
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


def _probe_command(param_name: str, value: Any) -> str:
    return (
        "python -c \"import json; "
        f"print(json.dumps({{'param_name': {param_name!r}, 'candidate_value': {value!r}}}, sort_keys=True))\""
    )


def _infer_probe(probe_name: str, param_name: str, value: Any, status: str) -> tuple[str, str]:
    if status == "pass":
        return f"{param_name}={value} passed a lightweight availability probe", "medium"
    if status == "guard_denied":
        return f"{param_name}={value} was not probed because command guard denied it", "low"
    if status == "timeout":
        return f"{param_name}={value} timed out during lightweight availability probing", "low"
    if status == "exception":
        return f"{param_name}={value} could not be probed because the runner raised an exception", "low"
    return f"{param_name}={value} failed a lightweight availability probe", "low"


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
                result: CommandResult = runner.run(f"safe_probe_{probe.name}_{safe_value}", _probe_command(probe.param_name, value))
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
            inference, confidence = _infer_probe(probe.name, probe.param_name, value, status)
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
