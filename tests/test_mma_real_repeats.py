"""Real native controls, not Paged Attention optimization/performance evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest

from kernel_opt_agent.diagnosis import diagnose_from_evidence_records
from kernel_opt_agent.diagnosis.evidence import observation_to_evidence
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.attribution_validation import ControlCapture, MmaCalibration


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcprofiler/real_c500_mma_repeats_20261030"


class RealMmaRepeatTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))

    def test_original_reports_and_source_hashes_survive_checkout(self):
        self.assertFalse(self.manifest["synthetic"])
        for item in self.manifest["artifacts"]:
            actual = hashlib.sha256((FIXTURE / item["path"]).read_bytes()).hexdigest()
            self.assertEqual(actual, item["stored_sha256"], item["path"])
            self.assertEqual(actual, item["source_sha256"], item["path"])
            self.assertEqual(item["storage_transform"], "none_original_bytes_preserved")
        for line in (FIXTURE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
            digest, name = line.split(maxsplit=1)
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), digest)
        source = ROOT / self.manifest["source"]["path"]
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), self.manifest["source"]["sha256"])
        recovery = json.loads((ROOT / "docs/artifacts/mma_calibration_recovery_20261030.json").read_text(encoding="utf-8"))
        failure = recovery["earlier_runtime_failure"]
        self.assertEqual(hashlib.sha256((ROOT / failure["representative_log"]).read_bytes()).hexdigest(), failure["stored_sha256"])
        self.assertEqual(failure["source_sha256"], failure["stored_sha256"])

    def test_six_independent_cases_keep_exact_raw_values_and_effective_config(self):
        expected = {
            ("idle", 1): 5.544957260637221, ("idle", 2): 4.8756551081005775,
            ("elementwise", 1): 0.0, ("elementwise", 2): 0.0,
            ("mma", 1): 10.040149127720056, ("mma", 2): 20.193788819875778,
        }
        cases = self.manifest["cases"]
        self.assertEqual(len({case["collection_id"] for case in cases}), 6)
        self.assertEqual({(case["mode"], case["repeat"]) for case in cases}, set(expected))
        device = self.manifest["environment"]["device_id"]
        for case in cases:
            self.assertEqual(case["status"], "collected")
            self.assertEqual(case["correctness"], {"passed": True, "max_error": 0.0})
            self.assertEqual(case["device_id"], device)
            parsed = parse_mcprofiler_case(FIXTURE / case["primary_report"])
            self.assertEqual(parsed.to_dict(), parse_mcprofiler_case(FIXTURE / case["primary_report"]).to_dict())
            observation = parsed.metric_map()["mma_duty"]
            self.assertEqual(observation.value, expected[case["mode"], case["repeat"]])
            self.assertEqual(observation.value, case["mma_duty"])
            self.assertTrue(observation.available)
            self.assertEqual(observation.device_id, device)
            self.assertEqual(observation.tool_version, "3.9.0.29")
            self.assertTrue(case["effective_conditions"]["profile_from_start"])
            self.assertEqual(case["effective_conditions"]["counts"], 100)
            stdout = (FIXTURE / (case["mode"] + "_" + str(case["repeat"])) / "stdout.log").read_text(encoding="utf-8")
            self.assertIn("CALIBRATION_CONTROL mode=" + case["mode"] + " max_error=0", stdout)
            self.assertNotIn("not found (required by", stdout)

    def test_real_idle_signal_and_repeated_mma_response_do_not_authorize_planning(self):
        captures = []
        for case in self.manifest["cases"]:
            parsed = parse_mcprofiler_case(FIXTURE / case["primary_report"])
            observation = parsed.metric_map()["mma_duty"]
            record = observation_to_evidence(observation, case["collection_id"])
            row = record.to_dict()
            self.assertFalse(row["planning_eligible"])
            self.assertEqual(row["attribution_status"], "unverified")
            self.assertEqual(row["confidence"], "low")
            self.assertIsNone(row["collection_scope"]["background_workload_excluded"])
            self.assertEqual({d.bottleneck_type for d in diagnose_from_evidence_records([record])}, {"insufficient_evidence"})
            captures.append(ControlCapture(case["mode"], case["mma_duty"], case["source_sha256"],
                hashlib.sha256((FIXTURE / "attribution_controls.ll").read_bytes()).hexdigest(),
                128 if case["mode"] == "mma" else 0,
                hashlib.sha256((FIXTURE / case["primary_report"]).read_bytes()).hexdigest()))
        result = MmaCalibration("3.9.0.29", "c63ea13089", "MetaX C500", tuple(captures)).evaluate()
        self.assertEqual(result, self.manifest["calibration"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("idle_control_not_quiet", result["reason_codes"])
        self.assertIn("metric_definition_unconfirmed", result["reason_codes"])
        self.assertTrue(result["response_differentiation_observed"])

    def test_compiled_ir_distinguishes_controls_without_claiming_final_isa(self):
        ir = (FIXTURE / "attribution_controls.ll").read_text(encoding="utf-8")
        bodies = dict(re.findall(r"define[^\n]*@attribution_(\w+)\([^\n]*\{(.*?)^\}", ir, re.M | re.S))
        self.assertNotIn("llvm.mxc.mma", bodies["elementwise"])
        self.assertEqual(len(re.findall(r"\bcall\b[^\n]*@llvm\.mxc\.mma", bodies["mma"])), 128)
        self.assertEqual(self.manifest["operator"], "native_controls_not_paged_attention")


if __name__ == "__main__":
    unittest.main()
