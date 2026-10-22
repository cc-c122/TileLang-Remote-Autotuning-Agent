from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kernel_opt_agent.diagnosis.evidence import observation_to_evidence, planning_evidence_allowed
from kernel_opt_agent.profiler.attribution import annotate_planning_eligibility
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.server.app import _result_payload
from tests.test_mcprofiler_attribution import _verified_row
from tests import test_mcprofiler_attribution as attribution_tests
from tests.test_mcprofiler_collection import FakeRemoteCollectionRunner
from tests.test_mcprofiler_real_c500_collection import FIXTURE, PRIMARY
from tests import test_source_optimizer as source_tests


class AttributionContractTests(unittest.TestCase):
    def test_documented_json_example_is_fail_closed_and_recomputable(self):
        path = Path(__file__).resolve().parents[1] / "docs/examples/mcprofiler_attribution_result.json"
        row = json.loads(path.read_text(encoding="utf-8"))["evidence_summary"][0]
        self.assertEqual(annotate_planning_eligibility(row), row)
        self.assertFalse(planning_evidence_allowed(row))

    def test_missing_attribution_and_injected_flags_are_ineligible(self):
        for row in ({}, {"planning_eligible": True}, {"source": "mcprofiler", "parse_warnings": 12}, {**_verified_row(), "source": "mcprofiler",
                    "planning_eligible": True, "eligibility_reasons": [], "tool_version": "3.9.0.29",
                    "device_id": "device_sha256:" + "a" * 64, "metric_definition": {"status": "documented"},
                    "collection_scope": {"requested_kernel": "fixture_kernel", "counter_scope": "kernel",
                                         "background_workload_excluded": True}}):
            computed = annotate_planning_eligibility(row)
            self.assertFalse(computed["planning_eligible"])
            self.assertTrue(computed["eligibility_reasons"])
            if row.get("source") == "mcprofiler":
                self.assertEqual(computed["attribution_status"], "unverified")
                self.assertEqual(computed["confidence"], "low")
                self.assertTrue(computed["parse_warnings"])

    def test_target_mismatch_is_explicit_even_when_every_claim_says_verified(self):
        row = {**_verified_row(), "source": "mcprofiler", "collection_scope": {"requested_kernel": "different_kernel"}}
        self.assertIn("target_kernel_mismatch", annotate_planning_eligibility(row)["eligibility_reasons"])

    def test_report_filename_is_not_a_statistical_scope_or_verified_claim(self):
        parsed = parse_mcprofiler_case(FIXTURE / PRIMARY)
        for observation in parsed.observations:
            row = observation_to_evidence(observation, "baseline").to_dict()
            self.assertEqual(row["scope"], "unknown")
            self.assertEqual(row["collection_scope"]["report_layout"], "named_kernel")
            self.assertEqual(row["collection_scope"]["counter_scope"], "unknown")
            self.assertEqual(row["tool_version"], "3.9.0.29")
            self.assertFalse(row["planning_eligible"])
            self.assertEqual(row["confidence"], "low")
        self.assertEqual(parsed.to_dict(), parse_mcprofiler_case(FIXTURE / PRIMARY).to_dict())

    def test_import_and_results_api_preserve_computed_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            import_report(FIXTURE / PRIMARY, results, "baseline")
            saved = [json.loads(line) for line in (results / "metric_observations.jsonl").read_text().splitlines()]
            api = _result_payload(results)
            self.assertEqual(api["evidence_summary"], saved)
            self.assertTrue(all(not row["planning_eligible"] for row in saved))
            forged = {**saved[0], "planning_eligible": True, "eligibility_reasons": []}
            (results / "metric_observations.jsonl").write_text(json.dumps(forged), encoding="utf-8")
            self.assertFalse(_result_payload(results)["evidence_summary"][0]["planning_eligible"])

    def test_collection_failure_does_not_publish_planning_evidence(self):
        class FailedCollectionRunner(FakeRemoteCollectionRunner):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, mode="failed")

        with tempfile.TemporaryDirectory() as tmp:
            record, _, _ = attribution_tests.McProfilerAttributionTests()._normal_trial(Path(tmp), FailedCollectionRunner)
            self.assertEqual(record["status"], "benchmark_ok")
            self.assertEqual(record["profiler"]["collection"]["status"], "failed")
            self.assertEqual(record["metric_observations"], [])

    def test_correctness_failure_rolls_back_without_profiler_evidence(self):
        # Exercise the existing rollback/performance gates without changing thresholds.
        case = source_tests.SourceEngineTests()
        _, _, result, original = case._run("correctness_failed")
        self.addCleanup(case.doCleanups)
        self.assertEqual(result.best_source_sha256, original)
        self.assertTrue(result.trials[0].rollback_verified)


if __name__ == "__main__":
    unittest.main()
