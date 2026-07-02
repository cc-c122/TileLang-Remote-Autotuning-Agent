from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel_opt_agent.runner.local_runner import CommandResult


@dataclass(frozen=True)
class SafeProbe:
    name: str
    command: str


SAFE_PROBES = [
    SafeProbe(
        "threads_probe",
        "python -c \"import json, os; print(json.dumps({'cpu_count': os.cpu_count()}))\"",
    ),
    SafeProbe(
        "vector_width_probe",
        "python -c \"import json, struct; print(json.dumps({'pointer_bits': struct.calcsize('P') * 8}))\"",
    ),
]


def _parse_candidate(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _infer_probe(probe_name: str, candidate: dict[str, Any], status: str) -> tuple[str, str]:
    if status != "pass":
        return "probe did not produce a usable inference", "low"
    if probe_name == "threads_probe":
        cpu_count = candidate.get("cpu_count")
        if isinstance(cpu_count, int) and cpu_count > 0:
            return f"host reports {cpu_count} CPU threads available for lightweight orchestration", "medium"
    if probe_name == "vector_width_probe":
        pointer_bits = candidate.get("pointer_bits")
        if isinstance(pointer_bits, int) and pointer_bits > 0:
            return f"python runtime pointer width is {pointer_bits} bits", "medium"
    return "probe completed but inference is limited", "low"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")


def run_safe_probes(runner: Any, results_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_dir = results_dir / "logs" / "hardware_probe"
    jsonl_path = results_dir / "hardware_probe.jsonl"
    records: list[dict[str, Any]] = []
    log_lines: list[str] = []

    for probe in SAFE_PROBES:
        stdout_path = log_dir / f"{probe.name}.stdout.log"
        stderr_path = log_dir / f"{probe.name}.stderr.log"
        status = "failed"
        candidate: dict[str, Any] = {}
        stdout = ""
        stderr = ""
        try:
            result: CommandResult = runner.run(f"safe_probe_{probe.name}", probe.command)
            stdout = result.stdout
            stderr = result.stderr
            if result.timeout:
                status = "timeout"
            elif result.returncode == 0:
                candidate = _parse_candidate(result.stdout)
                status = "pass" if candidate else "failed"
            else:
                status = "failed"
                stderr = result.error_message or result.stderr or f"return code {result.returncode}"
        except Exception as exc:
            status = "exception"
            stderr = str(exc)

        _write_text(stdout_path, stdout)
        _write_text(stderr_path, stderr)
        inference, confidence = _infer_probe(probe.name, candidate, status)
        record = {
            "probe_name": probe.name,
            "candidate": candidate,
            "status": status,
            "inference": inference,
            "source": "safe_probe",
            "confidence": confidence,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
        records.append(record)
        log_lines.append(f"safe probe {probe.name}: {status}")

    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    return records, log_lines
