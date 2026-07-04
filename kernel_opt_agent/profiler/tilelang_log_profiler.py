from __future__ import annotations

from .base import BaseProfiler, ProfilerResult
from .parser import parse_benchmark_output, parse_log_metrics


class TileLangLogProfiler(BaseProfiler):
    def collect(self, run_context):
        benchmark_stdout = run_context.get("benchmark_stdout") or ""
        benchmark_stderr = run_context.get("benchmark_stderr") or ""
        compile_log = run_context.get("compile_log") or ""
        generated_code = run_context.get("generated_code") or ""
        benchmark_metrics = parse_benchmark_output(benchmark_stdout)
        log_metrics = parse_log_metrics("\n".join([compile_log, benchmark_stderr, generated_code]))
        return ProfilerResult(
            latency=benchmark_metrics.get("latency"),
            tflops=benchmark_metrics.get("tflops"),
            estimated_hbm_bandwidth=benchmark_metrics.get("estimated_hbm_bandwidth"),
            register_count=log_metrics.get("register_count"),
            shared_memory_bytes=log_metrics.get("shared_memory_bytes"),
            private_memory_bytes=log_metrics.get("private_memory_bytes"),
            occupancy=log_metrics.get("occupancy"),
            warp_active_ratio=log_metrics.get("warp_active_ratio"),
            hbm_read_bandwidth=log_metrics.get("hbm_read_bandwidth"),
            hbm_write_bandwidth=log_metrics.get("hbm_write_bandwidth"),
            shared_bank_conflict=log_metrics.get("shared_bank_conflict"),
            memory_coalescing_efficiency=log_metrics.get("memory_coalescing_efficiency"),
            raw_logs={
                "benchmark_stdout": benchmark_stdout,
                "benchmark_stderr": benchmark_stderr,
                "compile_log": compile_log,
                "generated_code": generated_code,
            },
        )
