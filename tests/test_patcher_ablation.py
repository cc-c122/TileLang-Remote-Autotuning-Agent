from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from kernel_opt_agent.optimizer import AblationResult, write_ablation_csv, write_ablation_jsonl
from kernel_opt_agent.patcher import (
    PatchProposal,
    PatchTrial,
    apply_validated_patch,
    find_patch_regions,
    rollback_file,
    validate_patch_proposal,
    write_patch_trials_jsonl,
)
from kernel_opt_agent.profiler.base import ProfilerResult


SOURCE = """def kernel():
    # BEGIN_AGENT_PATCH: compute
    x = 1
    # END_AGENT_PATCH
    return x
"""


class PatcherAblationTests(unittest.TestCase):
    def test_profiler_latency_ms_alias_keeps_unified_name(self) -> None:
        result = ProfilerResult(latency_ms=1.5)
        self.assertEqual(result.latency_ms, 1.5)
        self.assertEqual(result.latency, 1.5)
        self.assertIn("latency_ms", result.to_dict())
        self.assertNotIn("latency", result.to_dict())

    def test_find_patch_regions(self) -> None:
        regions = find_patch_regions(SOURCE)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, "compute")
        self.assertEqual(regions[0].body_start_line, 3)

    def test_validator_rejects_unknown_or_forbidden_patch(self) -> None:
        missing = PatchProposal("vectorized_load", "load", "h", "e", "r", "x = 2\n")
        result = validate_patch_proposal(SOURCE, missing, {"compute"})
        self.assertFalse(result.ok)
        self.assertIn("target_region is not allowed: load", result.errors)

        forbidden = PatchProposal("bad", "compute", "h", "e", "r", "os.system('rm -rf /')\n")
        result = validate_patch_proposal(SOURCE, forbidden, {"compute"})
        self.assertFalse(result.ok)

    def test_apply_patch_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "kernel.py"
            target.write_text(SOURCE, encoding="utf-8")
            proposal = PatchProposal("improve_thread_mapping", "compute", "h", "e", "r", "    x = 2\n")
            applied = apply_validated_patch(target, proposal, root / "backups", root, {"compute"}, allowed_target=target)
            self.assertIn("x = 2", target.read_text(encoding="utf-8"))
            rollback_file(applied.rollback)
            self.assertEqual(target.read_text(encoding="utf-8"), SOURCE)

    def test_apply_patch_rejects_target_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            workspace = Path(workspace_tmp)
            outside = Path(outside_tmp)
            target = outside / "kernel.py"
            target.write_text(SOURCE, encoding="utf-8")
            proposal = PatchProposal("improve_thread_mapping", "compute", "h", "e", "r", "    x = 2\n")
            with self.assertRaisesRegex(ValueError, "target_path must be inside workspace_root"):
                apply_validated_patch(target, proposal, workspace / "backups", workspace, {"compute"})

    def test_apply_patch_rejects_non_allowed_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "kernel.py"
            other = root / "other.py"
            target.write_text(SOURCE, encoding="utf-8")
            other.write_text(SOURCE, encoding="utf-8")
            proposal = PatchProposal("improve_thread_mapping", "compute", "h", "e", "r", "    x = 2\n")
            with self.assertRaisesRegex(ValueError, "target_path is not the allowed target"):
                apply_validated_patch(target, proposal, root / "backups", root, {"compute"}, allowed_target=other)

    def test_patch_trial_is_json_serializable(self) -> None:
        trial = PatchTrial(
            trial_id="trial-1",
            patch_id="patch-a",
            optimization_name="vectorized_load",
            target_region="compute",
            hypothesis="improve load efficiency",
            expected_improvement="lower latency",
            risk="low",
            diagnosis_refs=["diag-1"],
            metrics_before={"latency_ms": 10.0},
        )
        data = trial.to_dict()
        self.assertEqual(data["status"], "proposed")
        self.assertTrue(data["rollback_available"])
        json.dumps(data)

    def test_patch_trial_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported patch trial status"):
            PatchTrial(
                trial_id="trial-1",
                patch_id="patch-a",
                optimization_name="vectorized_load",
                target_region="compute",
                hypothesis="h",
                expected_improvement="e",
                risk="r",
                status="unknown",
            )

    def test_patch_trials_jsonl_and_ablation_csv_write(self) -> None:
        trial = PatchTrial(
            trial_id="trial-1",
            patch_id="patch-a",
            optimization_name="vectorized_load",
            target_region="compute",
            hypothesis="improve load efficiency",
            expected_improvement="lower latency",
            risk="low",
        )
        ablation = AblationResult(
            trial_id="trial-1",
            patch_ids=["patch-a"],
            status="benchmark_ok",
            objective="latency",
            baseline_value=10.0,
            patched_value=8.0,
            improvement=20.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl_path = write_patch_trials_jsonl(root / "patch_trials.jsonl", [trial])
            write_ablation_jsonl(root / "ablation.jsonl", [ablation])
            csv_path = write_ablation_csv(root / "ablation_summary.csv", [ablation])
            record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["patch_id"], "patch-a")
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["patch_ids"], "patch-a")


if __name__ == "__main__":
    unittest.main()
