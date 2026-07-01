from __future__ import annotations

import math
import re
from dataclasses import dataclass


STRICT_RE = re.compile(
    r"BENCHMARK_RESULT\b(?=.*\blatency_ms=(?P<latency>nan|[-+]?[0-9]*\.?[0-9]+))?"
    r"(?=.*\btflops=(?P<tflops>nan|[-+]?[0-9]*\.?[0-9]+))?"
    r"(?=.*\bbandwidth_gbps=(?P<bandwidth>nan|[-+]?[0-9]*\.?[0-9]+))?",
    re.IGNORECASE,
)


@dataclass
class BenchmarkMetrics:
    latency: float | None = None
    tflops: float | None = None
    bandwidth: float | None = None
    parse_error: bool = False
    reason: str | None = None


def _to_float(value: str | None) -> float | None:
    if value is None or value.lower() == "nan":
        return None
    v = float(value)
    return None if math.isnan(v) else v


def parse_benchmark(stdout: str, regexes: dict[str, str | None]) -> BenchmarkMetrics:
    for line in stdout.splitlines():
        if "BENCHMARK_RESULT" in line:
            fields = dict(re.findall(r"(latency_ms|tflops|bandwidth_gbps)=(nan|[-+]?[0-9]*\.?[0-9]+)", line, flags=re.I))
            if fields:
                return BenchmarkMetrics(
                    latency=_to_float(fields.get("latency_ms")),
                    tflops=_to_float(fields.get("tflops")),
                    bandwidth=_to_float(fields.get("bandwidth_gbps")),
                    parse_error=False,
                    reason=None,
                )
    values: dict[str, float | None] = {}
    for name, pattern in regexes.items():
        values[name] = None
        if pattern:
            match = re.search(pattern, stdout, re.IGNORECASE)
            if match:
                values[name] = _to_float(match.group(1))
    if any(v is not None for v in values.values()):
        return BenchmarkMetrics(values.get("latency"), values.get("tflops"), values.get("bandwidth"), False, "regex fallback")
    return BenchmarkMetrics(parse_error=True, reason="benchmark metrics not found")

