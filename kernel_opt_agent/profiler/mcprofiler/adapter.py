from __future__ import annotations

from kernel_opt_agent.profiler.adapter import ProfileContext, ProfilerCapabilities, RawProfileArtifact
from kernel_opt_agent.profiler.base import MetricObservation, ProfilerResult
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler

from .collector import _tool_check_command, collect_remote_mcprofiler_case
from .report_parser import parse_mcprofiler_case


class McProfilerAdapter:
    def capabilities(self, context: ProfileContext | None = None) -> ProfilerCapabilities:
        warnings = []
        available = context is not None
        if context is None:
            warnings.append("remote runner is required to check collection tools")
        else:
            tools = [("client", context.request.executable)]
            if context.request.server_mode == "managed_external":
                tools.append(("server", context.request.server_executable))
            for label, executable in tools:
                try:
                    result = context.runner.run("mcprofiler_" + label + "_check", _tool_check_command(executable))
                    if result.returncode or result.guard_denied or result.timeout:
                        warnings.append("mcProfiler " + label + ": tool_unavailable_or_denied")
                        available = False
                except Exception as exc:
                    warnings.append("mcProfiler " + label + ": " + type(exc).__name__)
                    available = False
            if available:
                warnings.append("tool availability is not proof of successful counter collection")
        return ProfilerCapabilities("mcprofiler", available, True, ("mcprofiler_case",), ("mxmaca",), tuple(warnings))

    def collect(self, context: ProfileContext) -> RawProfileArtifact:
        result = collect_remote_mcprofiler_case(
            context.runner, context.results_dir, context.request, context.should_cancel,
            context.redactor, context.secret_values, context.import_output_dir,
            context.append_imported_records,
        )
        public = result.to_dict(context.redactor)
        return RawProfileArtifact(
            "mcprofiler", public["status"], public["mode"], public["collection_id"],
            public["reason_category"], public["case_dir"], public["artifact_manifest"],
            public["warnings"], context.results_dir, result,
            primary_report=public["primary_report"], partial_artifact_dir=public["partial_artifact_dir"],
        )

    def parse(self, artifact: RawProfileArtifact) -> list[MetricObservation]:
        path = artifact.case_path()
        return parse_mcprofiler_case(path).observations if path is not None else []

    def normalize(self, observations: list[MetricObservation]) -> ProfilerResult:
        return MxmacaProfiler.from_observations(observations)
