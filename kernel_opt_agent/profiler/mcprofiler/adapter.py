from __future__ import annotations

from kernel_opt_agent.profiler.adapter import ProfileContext, ProfilerCapabilities, RawProfileArtifact
from kernel_opt_agent.profiler.base import MetricObservation, ProfilerResult
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler

from .collector import collect_remote_mcprofiler_case
from .report_parser import parse_mcprofiler_case
from .tool_discovery import discover_mcprofiler, persist_capability


class McProfilerAdapter:
    def capabilities(self, context: ProfileContext | None = None) -> ProfilerCapabilities:
        warnings = []
        available = context is not None
        if context is None:
            warnings.append("remote runner is required to check collection tools")
        else:
            capability = discover_mcprofiler(
                context.runner, context.request.executable, context.request.server_executable,
                results_dir=context.results_dir, should_cancel=context.should_cancel, redactor=context.redactor,
                timeout_seconds=min(20, context.request.timeout_seconds),
            )
            persist_capability(context.results_dir, capability, context.redactor)
            available = capability.available
            warnings.extend(capability.warnings)
            if not available:
                warnings.append("mcProfiler discovery: " + capability.reason_category)
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
            target_reports=public["target_reports"],
        )

    def parse(self, artifact: RawProfileArtifact) -> list[MetricObservation]:
        if len(artifact.target_reports) > 1:
            root = (artifact.results_dir / artifact.case_dir).resolve()
            root.relative_to(artifact.results_dir.resolve())
            observations = []
            for report in artifact.target_reports:
                path = (root / report["report"]).resolve()
                path.relative_to(root)
                observations.extend(parse_mcprofiler_case(path).observations)
            return observations
        path = artifact.case_path()
        return parse_mcprofiler_case(path).observations if path is not None else []

    def normalize(self, observations: list[MetricObservation]) -> ProfilerResult:
        return MxmacaProfiler.from_observations(observations)
