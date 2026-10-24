from __future__ import annotations

import hashlib
import json
import math
import statistics
import tempfile
import unittest
from pathlib import Path

from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcprofiler/real_c500_paged_attention_20261022"


class RealPagedAttentionC500Tests(unittest.TestCase):
    def cases(self):
        for context in (128, 512):
            path = FIXTURE / f"ctx{context}"
            yield path, json.loads((path / "manifest.json").read_text(encoding="utf-8"))

    def test_original_report_bytes_and_manifests_are_preserved(self):
        for path, manifest in self.cases():
            self.assertFalse(manifest["synthetic"])
            self.assertEqual(manifest["fixture_kind"], "real_native_report_subset")
            self.assertEqual(manifest["collection_status"], "collected")
            for artifact in manifest["artifacts"]:
                actual = hashlib.sha256((path / artifact["path"]).read_bytes()).hexdigest()
                self.assertEqual(actual, artifact["source_sha256"])
                self.assertEqual(actual, artifact["stored_sha256"])
                self.assertEqual(artifact["storage_transform"], "none_original_bytes_preserved")
            for line in (path / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split(maxsplit=1)
                self.assertEqual(hashlib.sha256((path / name).read_bytes()).hexdigest(), digest)
            source = ROOT / "kernel_opt_agent/samples/paged_attention_decode" / manifest["sample_source"]["file"]
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), manifest["sample_source"]["sha256"])

    def test_paged_attention_metadata_and_metrics_parse_deterministically(self):
        for path, manifest in self.cases():
            report = path / manifest["primary_report"]
            parsed = parse_mcprofiler_case(report)
            self.assertEqual(parsed.to_dict(), parse_mcprofiler_case(report).to_dict())
            self.assertEqual(parsed.metadata["operator"], "Paged Attention Decode")
            self.assertNotEqual(parsed.metadata["target_subkernel"], "Gate/Up")
            self.assertEqual(parsed.metadata["shape"], manifest["shape"])
            self.assertEqual(parsed.metadata["dtype"], "fp16")
            self.assertIsNone(parsed.metadata["exec_id"])
            for name, expected in manifest["expected_metrics"].items():
                metric = parsed.metric_map()[name]
                self.assertEqual(metric.value, expected["value"], name)
                self.assertEqual(metric.unit, expected["unit"], name)
                self.assertEqual(metric.available, expected["available"], name)
            aggregate = parse_mcprofiler_case(path / "report_dumped_result.json")
            self.assertNotEqual(parsed.metric_map()["global_read_instructions"].value,
                                aggregate.metric_map()["global_read_instructions"].value)

    def test_real_counters_stay_ineligible_and_shared_cycles_keep_their_unit(self):
        for path, manifest in self.cases():
            result = MxmacaProfiler().collect({"mcprofiler_case_path": str(path / manifest["primary_report"])})
            self.assertIsNone(result.shared_bank_conflict)
            self.assertEqual(result.shared_conflict_cycles, manifest["expected_metrics"]["shared_conflict_cycles"]["value"])
            parsed = parse_mcprofiler_case(path / manifest["primary_report"])
            self.assertEqual(parsed.metric_map()["shared_conflict_cycles"].unit, "cycles_per_instruction")
            for observation in parsed.observations:
                row = observation.to_dict()
                self.assertEqual(row["attribution_status"], "unverified")
                self.assertEqual(row["scope"], "unknown")
                self.assertEqual(row["confidence"], "low")
                self.assertFalse(row["planning_eligible"])
                self.assertTrue(row["eligibility_reasons"])

    def test_report_import_writes_an_evidence_chain_without_strong_diagnoses(self):
        for path, manifest in self.cases():
            with tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary)
                result = import_report(path / manifest["primary_report"], out, "source-baseline")
                self.assertEqual(result["evidence_count"], 12)
                rows = [json.loads(line) for line in (out / "metric_observations.jsonl").read_text(encoding="utf-8").splitlines()]
                ids = {row["evidence_id"] for row in rows}
                self.assertTrue(all(not row["planning_eligible"] for row in rows))
                diagnoses = [json.loads(line) for line in (out / "diagnoses.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertTrue(diagnoses)
                for diagnosis in diagnoses:
                    self.assertEqual(diagnosis["bottleneck_type"], "insufficient_evidence")
                    self.assertTrue(set(diagnosis["evidence_ids"]).issubset(ids))
                for artifact in out.iterdir():
                    serialized = artifact.read_text(encoding="utf-8")
                    self.assertNotIn("root+vm-", serialized)
                    self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", serialized)
                self.assertTrue((out / "profiler_results.jsonl").is_file())

    def test_real_baseline_timings_preserve_single_call_samples_and_correctness(self):
        for _, manifest in self.cases():
            self.assertTrue(manifest["correctness"]["passed"])
            self.assertEqual(manifest["correctness"]["tolerance"], {"atol": 0.002, "rtol": 0.002})
            self.assertGreaterEqual(manifest["correctness"]["max_error"], 0)
            self.assertEqual(len(manifest["benchmark"]["measurement_records"]), 5)
            for record in manifest["benchmark"]["measurement_records"]:
                self.assertEqual(record["warmup_runs"], 3)
                self.assertEqual(record["measurement_runs"], 5)
                self.assertEqual(record["latency_scope"], "operator_end_to_end")
                samples = record["raw_samples_ms"]
                self.assertEqual(len(samples), 5)
                self.assertTrue(all(math.isfinite(value) and value > 0 for value in samples))
                self.assertEqual(record["median_ms"], statistics.median(samples))
                self.assertAlmostEqual(record["stddev_ms"], statistics.stdev(samples))
                self.assertGreaterEqual(record["p90_ms"], min(samples))
                self.assertLessEqual(record["p90_ms"], max(samples))


if __name__ == "__main__":
    unittest.main()
