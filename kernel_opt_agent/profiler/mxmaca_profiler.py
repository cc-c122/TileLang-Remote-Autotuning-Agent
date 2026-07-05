from __future__ import annotations

from .base import BaseProfiler, ProfilerResult
from .parser import parse_benchmark_output, parse_log_metrics


class MxmacaProfiler(BaseProfiler):
    def collect(self, run_context):
        benchmark_stdout = run_context.get("benchmark_stdout") or ""
        profiler_stdout = run_context.get("profiler_stdout") or ""
        profiler_stderr = run_context.get("profiler_stderr") or ""
        compile_log = run_context.get("compile_log") or ""
        benchmark_metrics = parse_benchmark_output(benchmark_stdout)
        parsed = parse_log_metrics("\n".join([profiler_stdout, profiler_stderr, compile_log]))
        return ProfilerResult(
            latency_ms=benchmark_metrics.get("latency"),
            tflops=benchmark_metrics.get("tflops"),
            estimated_hbm_bandwidth=benchmark_metrics.get("estimated_hbm_bandwidth"),
            register_count=parsed.get("register_count"),
            shared_memory_bytes=parsed.get("shared_memory_bytes"),
            private_memory_bytes=parsed.get("private_memory_bytes"),
            occupancy=parsed.get("occupancy"),
            warp_active_ratio=parsed.get("warp_active_ratio"),
            hbm_read_bandwidth=parsed.get("hbm_read_bandwidth"),
            hbm_write_bandwidth=parsed.get("hbm_write_bandwidth"),
            shared_bank_conflict=parsed.get("shared_bank_conflict"),
            memory_coalescing_efficiency=parsed.get("memory_coalescing_efficiency"),
            raw_logs={
                "benchmark_stdout": benchmark_stdout,
                "profiler_stdout": profiler_stdout,
                "profiler_stderr": profiler_stderr,
                "compile_log": compile_log,
                "todo": "MXMACA profiler command integration is not implemented yet; only explicit log metrics are parsed.",
            },
        )
