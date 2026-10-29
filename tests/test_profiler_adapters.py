from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from kernel_opt_agent.profiler.adapter import ProfileContext, ProfilerAdapter, RawProfileArtifact
from kernel_opt_agent.profiler.mcprofiler import McProfilerCollectionRequest
from kernel_opt_agent.profiler.registry import collect_mcprofiler_case, get_profiler_adapter
from tests.test_mcprofiler_collection import FakeRemoteCollectionRunner


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mcprofiler"


class ProfilerAdapterTests(unittest.TestCase):
    def request(self):
        return McProfilerCollectionRequest("python3 kernel.py", "native-collection", "baseline", "a" * 64)

    def artifact(self):
        return RawProfileArtifact("mcprofiler", "imported", "report_import", "fixture", "none",
                                  "real_v8_tc1_gate", [], [], FIXTURES)

    def test_real_report_parse_is_deterministic_and_normalization_is_conservative(self):
        adapter = get_profiler_adapter("mcprofiler")
        self.assertIsInstance(adapter, ProfilerAdapter)
        first = adapter.parse(self.artifact())
        second = adapter.parse(self.artifact())
        self.assertEqual([item.to_dict() for item in first], [item.to_dict() for item in second])
        result = adapter.normalize(first)
        self.assertAlmostEqual(result.l2c_hit_rate, 46.10, places=2)
        self.assertAlmostEqual(result.dnoc_read_average_latency, 283.70, places=2)
        self.assertIsNone(result.shared_bank_conflict)
        self.assertIsNone(result.latency_ms)
        self.assertIsNone(result.private_memory_bytes)

    def test_registry_rejects_unknown_provider_and_keeps_mxmaca_alias(self):
        self.assertEqual(type(get_profiler_adapter("mxmaca")), type(get_profiler_adapter("mcprofiler")))
        with self.assertRaisesRegex(ValueError, "not implemented"):
            get_profiler_adapter("unimplemented-tool")

    def test_missing_tools_are_not_claimed_as_validated_collection(self):
        adapter = get_profiler_adapter("mcprofiler")
        self.assertFalse(adapter.capabilities().collection_available)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = ProfileContext(FakeRemoteCollectionRunner(root / "remote", "missing_tool"), root / "results", self.request())
            capability = adapter.capabilities(context)
            self.assertFalse(capability.collection_available)
            self.assertFalse(capability.collection_verified)
            artifact = adapter.collect(context)
            self.assertEqual(artifact.status, "unsupported")
            self.assertEqual(artifact.reason_category, "tool_unavailable")
            self.assertTrue((context.results_dir / "mcprofiler_collection.jsonl").is_file())
            self.assertEqual(adapter.parse(artifact), [])

    def test_tool_presence_is_not_collection_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = ProfileContext(FakeRemoteCollectionRunner(root / "remote"), root / "results", self.request())
            capability = get_profiler_adapter("mcprofiler").capabilities(context)
            self.assertTrue(capability.collection_available)
            self.assertFalse(capability.collection_verified)
            self.assertTrue(capability.warnings)

    def test_capability_exception_does_not_echo_secret(self):
        runner = Mock()
        runner.info.remote_workspace = "/managed/task"
        runner.denied_commands = []
        runner.run_cancellable.side_effect = RuntimeError("sensitive-value-119")
        with tempfile.TemporaryDirectory() as temporary:
            capability = get_profiler_adapter("mcprofiler").capabilities(ProfileContext(runner, Path(temporary), self.request()))
        self.assertNotIn("sensitive-value-119", json.dumps(capability.to_dict()))

    def test_artifact_path_cannot_escape_root(self):
        artifact = self.artifact()
        artifact.case_dir = "../outside"
        with self.assertRaises(ValueError):
            get_profiler_adapter("mcprofiler").parse(artifact)

    def test_context_credentials_and_native_object_are_not_serialized(self):
        context = ProfileContext(object(), Path("results"), self.request(), secret_values=("sensitive-value-119",))
        self.assertNotIn("sensitive-value-119", repr(context))
        artifact = self.artifact()
        artifact.native_result = {"password": "sensitive-value-119"}
        self.assertNotIn("sensitive-value-119", json.dumps(artifact.to_dict()))

    def test_legacy_collection_result_schema_remains_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = collect_mcprofiler_case(FakeRemoteCollectionRunner(root / "remote"), root / "results", self.request())
            self.assertEqual(result.status, "collected")
            self.assertEqual(result.to_dict()["schema_version"], "v2.mcprofiler_collection.v1")
            self.assertEqual(result.to_dict()["source_trial_id"], "baseline")


if __name__ == "__main__":
    unittest.main()
