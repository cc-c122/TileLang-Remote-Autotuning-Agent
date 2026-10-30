from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from kernel_opt_agent.diagnosis.evidence import observation_to_evidence
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.kernel_mapping import map_paged_attention_kernels


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mcprofiler/real_c500_paged_mapping_20261029"
SOURCE = ROOT / "kernel_opt_agent/samples/paged_attention_decode/_upstream_sparse_gqa_decode_paged.py"


class KernelMappingTests(unittest.TestCase):
    def _mapping(self, root=FIXTURE, reports=None):
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        return map_paged_attention_kernels(SOURCE, root / "generated_device.cc", root / "generated_host.cc",
            reports if reports is not None else sorted(root.glob("*_dumped_result.json")),
            tuple(manifest["source"]["modification_lines_inspected_only"]))

    def test_source_and_original_artifact_hashes_are_preserved(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["synthetic"])
        self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(SOURCE.read_bytes()).hexdigest())
        for artifact in manifest["artifacts"]:
            self.assertEqual(artifact["storage_transform"], "none_original_bytes_preserved")
            self.assertEqual(artifact["source_sha256"], artifact["stored_sha256"])
            self.assertEqual(artifact["stored_sha256"], hashlib.sha256((FIXTURE / artifact["path"]).read_bytes()).hexdigest())

    def test_split_and_combine_mapping_uses_buffers_codegen_and_host_not_report_name(self):
        mapping = self._mapping()
        roles = {row["source_region"]["role_candidate"]: row for row in mapping["mappings"]}
        self.assertEqual(set(roles), {"split", "combine"})
        self.assertTrue(roles["split"]["source_region"]["contains_modification"])
        self.assertFalse(roles["combine"]["source_region"]["contains_modification"])
        self.assertEqual(roles["split"]["generated_candidates"][0]["mma_call_sites"], 2)
        self.assertEqual(roles["combine"]["generated_candidates"][0]["mma_call_sites"], 0)
        self.assertEqual([row["kernel_name"] for row in mapping["static_host_calls"]], ["main_kernel", "main_kernel_1"])
        self.assertFalse(mapping["runtime_attribution_verified"])
        self.assertTrue(all(not row["planning_eligible"] and row["runtime_launches"] == [] for row in roles.values()))

    def test_mapping_is_deterministic_and_matches_saved_derivation(self):
        self.assertEqual(self._mapping(), self._mapping())
        self.assertEqual(self._mapping(), json.loads((FIXTURE / "kernel_mapping.json").read_text(encoding="utf-8")))

    def test_renaming_generated_kernels_does_not_change_structural_roles_or_verify_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
            for name in ("generated_device.cc", "generated_host.cc"):
                path = root / name
                text = path.read_text(encoding="utf-8").replace("main_kernel_1", "renamed_combine").replace("main_kernel", "renamed_split")
                path.write_text(text, encoding="utf-8")
            mapping = self._mapping(root)
            self.assertEqual({row["source_region"]["role_candidate"]: row["generated_candidates"][0]["name"]
                              for row in mapping["mappings"]}, {"split": "renamed_split", "combine": "renamed_combine"})
            self.assertTrue(all(row["report_candidates"] == [] for row in mapping["mappings"]))
            self.assertFalse(mapping["runtime_attribution_verified"])

    def test_repeated_report_slots_are_not_conflated_with_unique_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp) / "7_main_kernel_dumped_result.json"
            shutil.copyfile(FIXTURE / "1_main_kernel_dumped_result.json", duplicate)
            reports = sorted(FIXTURE.glob("*_dumped_result.json")) + [duplicate]
            mapping = self._mapping(reports=reports)
            split = next(row for row in mapping["mappings"] if row["source_region"]["role_candidate"] == "split")
            self.assertEqual(len(split["report_candidates"]), 2)
            self.assertTrue(all(row["launch_id"] is None for row in split["report_candidates"]))
            self.assertFalse(split["planning_eligible"])

    def test_named_report_and_config_filter_still_cannot_prove_actual_scope(self):
        config = json.loads((FIXTURE / "mcProfiler.json").read_text(encoding="utf-8"))
        self.assertEqual(config["conditions"]["kernelnames"], ["main_kernel"])
        self.assertTrue((FIXTURE / "2_main_kernel_1_dumped_result.json").is_file())
        for name in ("1_main_kernel_dumped_result.json", "2_main_kernel_1_dumped_result.json"):
            parsed = parse_mcprofiler_case(FIXTURE / name)
            observation = next(item for item in parsed.observations if item.metric_name == "mma_duty")
            row = observation_to_evidence(observation, "source-baseline").to_dict()
            self.assertEqual(row["scope"], "unknown")
            self.assertEqual(row["attribution_status"], "unverified")
            self.assertFalse(row["planning_eligible"])
            self.assertEqual(row["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
