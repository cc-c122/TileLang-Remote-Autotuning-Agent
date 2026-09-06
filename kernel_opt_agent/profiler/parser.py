from __future__ import annotations

import re
from typing import Any

from kernel_opt_agent.benchmark.parser import parse_benchmark


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int_number(value: str | None) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _first_number(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return _number(match.group(1))
    return None


def _first_int(patterns: list[str], text: str, flags: int = re.IGNORECASE) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return _int_number(match.group(1))
    return None


def parse_benchmark_output(text: str) -> dict[str, Any]:
    parsed = parse_benchmark(
        text,
        {
            "latency": r"latency\s*[:=]\s*([0-9.]+)",
            "tflops": r"tflops\s*[:=]\s*([0-9.]+)",
            "bandwidth": r"bandwidth(?:_gbps)?\s*[:=]\s*([0-9.]+)",
        },
    )
    return {
        "latency": parsed.latency,
        "tflops": parsed.tflops,
        "estimated_hbm_bandwidth": parsed.bandwidth,
    }


def parse_log_metrics(text: str) -> dict[str, Any]:
    shared_kb = _first_number(
        [
            r"shared(?:_memory)?(?:_kb)?\s*[:=]\s*([0-9.]+)\s*kb\b",
            r"shared memory\s*[:=]\s*([0-9.]+)\s*kb\b",
        ],
        text,
    )
    private_kb = _first_number(
        [
            r"(?:private|local)(?:_memory)?(?:_kb)?\s*[:=]\s*([0-9.]+)\s*kb\b",
            r"(?:private|local) memory\s*[:=]\s*([0-9.]+)\s*kb\b",
        ],
        text,
    )
    return {
        "register_count": _first_int(
            [
                r"register_count\s*[:=]\s*([0-9]+)",
                r"registers?\s*[:=]\s*([0-9]+)",
                r"([0-9]+)\s+registers?\b",
            ],
            text,
        ),
        "shared_memory_bytes": _first_int(
            [
                r"shared_memory_bytes\s*[:=]\s*([0-9]+)",
                r"shared memory\s*[:=]\s*([0-9]+)\s*bytes?\b",
            ],
            text,
        )
        or (int(shared_kb * 1024) if shared_kb is not None else None),
        "private_memory_bytes": _first_int(
            [
                r"private_memory_bytes\s*[:=]\s*([0-9]+)",
                r"local_memory_bytes\s*[:=]\s*([0-9]+)",
                r"(?:private|local) memory\s*[:=]\s*([0-9]+)\s*bytes?\b",
            ],
            text,
        )
        or (int(private_kb * 1024) if private_kb is not None else None),
        "occupancy": _first_number([r"occupancy\s*[:=]\s*([0-9.]+)"], text),
        "warp_active_ratio": _first_number(
            [r"warp_active_ratio\s*[:=]\s*([0-9.]+)", r"warp active(?: ratio)?\s*[:=]\s*([0-9.]+)"],
            text,
        ),
        "hbm_read_bandwidth": _first_number(
            [r"hbm_read_bandwidth(?:_gbps)?\s*[:=]\s*([0-9.]+)", r"hbm read(?: bandwidth)?\s*[:=]\s*([0-9.]+)"],
            text,
        ),
        "hbm_write_bandwidth": _first_number(
            [r"hbm_write_bandwidth(?:_gbps)?\s*[:=]\s*([0-9.]+)", r"hbm write(?: bandwidth)?\s*[:=]\s*([0-9.]+)"],
            text,
        ),
        "shared_bank_conflict": _first_number(
            [r"shared_bank_conflict\s*[:=]\s*([0-9.]+)", r"bank conflicts?\s*[:=]\s*([0-9.]+)"],
            text,
        ),
        "memory_coalescing_efficiency": _first_number(
            [r"memory_coalescing_efficiency\s*[:=]\s*([0-9.]+)", r"coalescing efficiency\s*[:=]\s*([0-9.]+)"],
            text,
        ),
    }
