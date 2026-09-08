from __future__ import annotations

from .base import BaseProfiler, ProfilerResult
from .mcprofiler import parse_mcprofiler_case
from .mcprofiler.report_parser import redact_mcprofiler_sensitive
from .parser import parse_benchmark_output, parse_log_metrics


def _observation_value(metric_map, name):
    item = metric_map.get(name)
    if item is None or not item.available:
        return None
    return item.value


class MxmacaProfiler(BaseProfiler):
    def collect(self, run_context):
        case_path = run_context.get("mcprofiler_case_path") or run_context.get("profiler_case_path")
        if case_path:
            parsed_case = parse_mcprofiler_case(case_path)
            public_case = parsed_case.to_dict()
            metric_map = parsed_case.metric_map()
            benchmark_metrics = parse_benchmark_output(run_context.get("benchmark_stdout") or "")
            return ProfilerResult(
                latency_ms=benchmark_metrics.get("latency"),
                tflops=benchmark_metrics.get("tflops"),
                estimated_hbm_bandwidth=benchmark_metrics.get("estimated_hbm_bandwidth"),
                vl1_hit_rate=_observation_value(metric_map, "vl1_hit_rate"),
                l2c_hit_rate=_observation_value(metric_map, "l2c_hit_rate"),
                dnoc_read_average_latency=_observation_value(metric_map, "dnoc_read_average_latency"),
                shared_memory_access_efficiency=_observation_value(metric_map, "shared_memory_access_efficiency"),
                shared_conflict_cycles=_observation_value(metric_map, "shared_conflict_cycles"),
                achieved_waves=_observation_value(metric_map, "achieved_waves"),
                dispatched_waves=_observation_value(metric_map, "dispatched_waves"),
                mma_duty=_observation_value(metric_map, "mma_duty"),
                observations=parsed_case.observations,
                raw_artifact_refs=public_case["artifacts"],
                raw_logs={
                    "benchmark_stdout": run_context.get("benchmark_stdout") or "",
                    "mcprofiler_metadata": str(redact_mcprofiler_sensitive(parsed_case.metadata)),
                    "mcprofiler_warnings": "\n".join(redact_mcprofiler_sensitive(parsed_case.warnings)),
                    "unknown_field_count": str(len(parsed_case.unknown_rows)),
                },
            )
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
