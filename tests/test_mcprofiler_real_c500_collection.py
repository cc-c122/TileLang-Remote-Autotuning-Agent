from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from kernel_opt_agent.diagnosis import diagnose_from_evidence_records, profiler_observations_to_evidence
from kernel_opt_agent.profiler.adapter import ProfileContext, RawProfileArtifact
from kernel_opt_agent.profiler.mcprofiler import McProfilerCollectionRequest, parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.profiler.registry import get_profiler_adapter
from tests.test_mcprofiler_collection import FakeRemoteCollectionRunner


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcprofiler/real_c500_native_smoke_20261021"
PRIMARY = "1_profiler_vector_axpby_dumped_result.json"


class RealC500McProfilerTests(unittest.TestCase):
    def test_original_report_bytes_and_sample_source_match_manifest(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text())
        self.assertFalse(manifest["synthetic"])
        sums = dict(line.split(maxsplit=1)[::-1] for line in (FIXTURE / "SHA256SUMS.txt").read_text().splitlines())
        for row in manifest["artifacts"]:
            with self.subTest(artifact=row["path"]):
                actual = hashlib.sha256((FIXTURE / row["path"]).read_bytes()).hexdigest()
                self.assertEqual(actual, row["stored_sha256"])
                self.assertEqual(actual, row["source_sha256"])
                self.assertEqual(actual, sums[row["path"]])
                self.assertEqual(row["storage_transform"], "none_original_bytes_preserved")
        self.assertEqual(hashlib.sha256((ROOT / manifest["source_file"]).read_bytes()).hexdigest(), manifest["source_sha256"])

    def test_named_kernel_report_is_deterministic_and_distinct_from_aggregate(self):
        first = parse_mcprofiler_case(FIXTURE / PRIMARY)
        second = parse_mcprofiler_case(FIXTURE / PRIMARY)
        self.assertEqual(first.to_dict(), second.to_dict())
        metrics = first.metric_map()
        self.assertEqual(len(metrics), 12)
        self.assertEqual(metrics["global_read_instructions"].value, 851256)
        self.assertEqual(metrics["global_write_instructions"].value, 605958)
        self.assertEqual(metrics["private_read_instructions"].value, 0)
        self.assertEqual(metrics["private_write_instructions"].value, 0)
        self.assertEqual(metrics["l2c_hit_rate"].value, 57.636304836736265)
        self.assertEqual(metrics["dnoc_read_average_latency"].value, 345.5125730911173)
        self.assertEqual(metrics["shared_conflict_cycles"].unit, "cycles_per_instruction")
        self.assertEqual(metrics["achieved_waves"].value, 278754.0)
        self.assertEqual(metrics["dispatched_waves"].value, 270566.0)
        aggregate = parse_mcprofiler_case(FIXTURE)
        self.assertNotEqual(first.artifacts["artifact_id"], aggregate.artifacts["artifact_id"])
        self.assertNotEqual(metrics["global_read_instructions"].value, aggregate.metric_map()["global_read_instructions"].value)
        self.assertEqual(first.metadata["operator"], "native_elementwise_axpby")
        self.assertNotIn("Paged Attention", first.metadata["target_subkernel"])

    def test_report_import_emits_evidence_and_only_insufficient_diagnosis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = import_report(FIXTURE / PRIMARY, root / "first", "native-baseline")
            second = import_report(FIXTURE / PRIMARY, root / "second", "native-baseline")
            self.assertEqual(first["evidence_count"], 12)
            for filename in ("profiler_results.jsonl", "metric_observations.jsonl", "diagnoses.jsonl"):
                self.assertEqual((root / "first" / filename).read_bytes(), (root / "second" / filename).read_bytes())
            self.assertEqual(first["artifact_id"], second["artifact_id"])
            profiler = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE / PRIMARY)})
            self.assertIsNone(profiler.latency_ms)
            self.assertIsNone(profiler.shared_bank_conflict)
            self.assertIsNone(profiler.private_memory_bytes)
            self.assertIsNone(profiler.occupancy)
            evidence = profiler_observations_to_evidence(profiler, "native-baseline")
            ids = {row.evidence_id for row in evidence}
            diagnoses = diagnose_from_evidence_records(evidence)
            self.assertTrue(diagnoses)
            for diagnosis in diagnoses:
                self.assertEqual(diagnosis.bottleneck_type, "insufficient_evidence")
                self.assertEqual(diagnosis.confidence, "low")
                self.assertTrue(set(diagnosis.evidence_ids).issubset(ids))

    def test_collector_and_adapter_select_named_report_without_overwriting_native_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = FakeRemoteCollectionRunner(root / "remote", fixture=FIXTURE)
            request = McProfilerCollectionRequest(
                target_command="./kernel_probe --mode profile", case_name="native-c500",
                source_trial_id="native-baseline", source_sha256="a" * 64,
                kernel_names=("profiler_vector_axpby",), per_kernel=True,
            )
            adapter = get_profiler_adapter("mcprofiler")
            artifact = adapter.collect(ProfileContext(runner, root / "results", request))
            self.assertEqual(artifact.status, "collected")
            self.assertEqual(artifact.primary_report, PRIMARY)
            self.assertEqual(artifact.native_result.execution_manifest["report_scope"], "named_kernel")
            self.assertEqual(artifact.native_result.execution_manifest["report_sha256"], hashlib.sha256((FIXTURE / PRIMARY).read_bytes()).hexdigest())
            self.assertEqual(adapter.normalize(adapter.parse(artifact)).l2c_hit_rate, 57.636304836736265)
            self.assertEqual(artifact.case_path().read_bytes(), (FIXTURE / PRIMARY).read_bytes())
            self.assertTrue((artifact.case_path().parent / "report_dumped_result.json").is_file())

    def test_named_report_missing_or_ambiguous_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for kernel in ("not_this_kernel", "profiler_vector_axpby"):
                with self.subTest(kernel=kernel):
                    runner = FakeRemoteCollectionRunner(root / kernel, fixture=FIXTURE)
                    if kernel == "profiler_vector_axpby":
                        original = runner.run_cancellable

                        def ambiguous(*args, **kwargs):
                            result = original(*args, **kwargs)
                            if args[0] == "mcprofiler_discovery":
                                return result
                            report = next(runner.remote_root.rglob(PRIMARY))
                            # Distinct indices of one symbol are valid repeats;
                            # duplicated index/location remains ambiguous.
                            duplicate = report.parent / "duplicate" / report.name
                            duplicate.parent.mkdir()
                            duplicate.write_bytes(report.read_bytes())
                            return result

                        runner.run_cancellable = ambiguous
                    request = McProfilerCollectionRequest(
                        target_command="./kernel_probe --mode profile", case_name="native-c500",
                        source_trial_id="baseline", source_sha256="a" * 64,
                        kernel_names=(kernel,), per_kernel=True,
                    )
                    artifact = get_profiler_adapter("mcprofiler").collect(ProfileContext(runner, root / (kernel + "-results"), request))
                    self.assertEqual((artifact.status, artifact.reason_category), ("failed", "target_report_not_found"))
                    self.assertFalse(artifact.native_result.metric_observations)

    def test_selected_report_cannot_escape_case_directory(self):
        artifact = RawProfileArtifact("mcprofiler", "imported", "report_import", "case", "none",
                                      FIXTURE.name, [], [], FIXTURE.parent, primary_report="../../outside.json")
        with self.assertRaises(ValueError):
            artifact.case_path()

    def test_real_benchmark_keeps_five_individual_gpu_event_samples(self):
        benchmark = json.loads((FIXTURE / "benchmark.json").read_text())
        self.assertEqual(benchmark["warmup"], 3)
        self.assertEqual(benchmark["measurements"], 5)
        self.assertEqual(benchmark["timing"], "GPU events")
        self.assertEqual(len(benchmark["samples_ms"]), 5)
        self.assertTrue(all(math.isfinite(value) and value > 0 for value in benchmark["samples_ms"]))
        self.assertEqual(sorted(benchmark["samples_ms"])[2], benchmark["median_ms"])
        self.assertIn("status=PASS max_error=0", (FIXTURE / "correctness.stdout.log").read_text())


if __name__ == "__main__":
    unittest.main()
