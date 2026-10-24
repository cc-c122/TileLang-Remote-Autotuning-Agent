from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from kernel_opt_agent.diagnosis import diagnose_from_evidence_records
from kernel_opt_agent.diagnosis.evidence import observation_to_evidence
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcprofiler/real_c500_attribution_controls_20261022"


class RealC500AttributionTests(unittest.TestCase):
    def test_original_bytes_and_source_hashes_match_manifest(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["artifacts"]:
            content = (FIXTURE / item["path"]).read_bytes()
            actual = hashlib.sha256(content).hexdigest()
            self.assertEqual(actual, item["stored_sha256"], item["path"])
            self.assertEqual(actual, item["source_sha256"], item["path"])
            self.assertEqual(item["storage_transform"], "none_original_bytes_preserved")
        source = ROOT / manifest["source"]["path"]
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), manifest["source"]["sha256"])
        for line in (FIXTURE / "SHA256SUMS.txt").read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), digest, name)

    def test_device_ir_confirms_distinct_positive_and_negative_controls(self):
        text = (FIXTURE / "attribution_controls.ll").read_text(encoding="utf-8")
        bodies = dict(re.findall(r"define[^\n]*@attribution_(\w+)\([^\n]*\{(.*?)^\}", text, re.S | re.M))
        self.assertNotIn("llvm.mxc.mma", bodies["elementwise"])
        self.assertEqual(bodies["mma"].count("@llvm.mxc.mma.f32.16x16x16f16"), 128)

    def test_real_calibration_is_not_promoted_to_verified_attribution(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        for case in manifest["cases"]:
            path = FIXTURE / case["mode"] / case["primary_report"]
            parsed = parse_mcprofiler_case(path)
            self.assertEqual(parsed.to_dict(), parse_mcprofiler_case(path).to_dict())
            records = [observation_to_evidence(obs, case["collection_id"], i) for i, obs in enumerate(parsed.observations)]
            for record in records:
                row = record.to_dict()
                self.assertFalse(row["planning_eligible"])
                self.assertEqual(row["attribution_status"], "unverified")
                self.assertEqual(row["scope"], "unknown")
                self.assertEqual(row["tool_version"], "3.9.0.29")
                self.assertRegex(row["device_id"], r"^device_sha256:[0-9a-f]{64}$")
                self.assertEqual(row["collection_scope"]["profile_from_start_actual"], True)
                self.assertEqual(row["collection_scope"]["counts_actual"], 100)
                self.assertIsNone(row["collection_scope"]["background_workload_excluded"])
            self.assertEqual({d.bottleneck_type for d in diagnose_from_evidence_records(records)}, {"insufficient_evidence"})
            metrics = parsed.metric_map()
            if case["mode"] == "idle":
                self.assertIsNone(metrics["mma_duty"].value)
                self.assertFalse(metrics["mma_duty"].available)
            else:
                self.assertEqual(metrics["mma_duty"].value, case["metrics"]["AP MMA Duty ratio"])
                self.assertTrue(metrics["mma_duty"].available)
        self.assertEqual(manifest["cases"][1]["metrics"]["AP MMA Duty ratio"], 0)
        self.assertEqual(manifest["cases"][2]["metrics"]["AP MMA Duty ratio"], 22.141887006642296)


if __name__ == "__main__":
    unittest.main()
