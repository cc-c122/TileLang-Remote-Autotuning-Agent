from __future__ import annotations

from pathlib import Path

from .adapter import ProfileContext, ProfilerAdapter


def get_profiler_adapter(tool: str) -> ProfilerAdapter:
    if tool in {"mcprofiler", "mxmaca"}:
        from .mcprofiler.adapter import McProfilerAdapter

        return McProfilerAdapter()
    raise ValueError(f"native profiler adapter is not implemented: {tool}")


def collect_mcprofiler_case(runner, results_dir: Path, request, should_cancel=None,
                           redactor=None, secret_values=(), import_output_dir=None,
                           append_imported_records=False):
    """Keep existing task consumers on the original mcProfiler result schema."""
    context = ProfileContext(runner, results_dir, request, should_cancel, redactor,
                             secret_values, import_output_dir, append_imported_records)
    return get_profiler_adapter("mcprofiler").collect(context).native_result
