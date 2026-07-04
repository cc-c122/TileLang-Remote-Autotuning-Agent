from __future__ import annotations

from .base import BaseProfiler, ProfilerResult
from .parser import parse_benchmark_output


class DummyProfiler(BaseProfiler):
    def collect(self, run_context):
        benchmark_stdout = run_context.get("benchmark_stdout") or ""
        metrics = parse_benchmark_output(benchmark_stdout)
        return ProfilerResult(
            latency=metrics.get("latency"),
            tflops=metrics.get("tflops"),
            estimated_hbm_bandwidth=metrics.get("estimated_hbm_bandwidth"),
            raw_logs={"benchmark_stdout": benchmark_stdout},
        )
