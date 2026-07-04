from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from kernel_opt_agent.optimizer import AblationResult, write_ablation_csv, write_ablation_jsonl
from kernel_opt_agent.patcher import (
    PatchProposal,
    apply_validated_patch,
    find_patch_regions,
    rollback_file,
    validate_patch_proposal,
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
        result = ProfilerResult(latency=1.5)
        self.assertEqual(result.latency_ms, 1.5)

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
            applied = apply_validated_patch(target, proposal, root / "backups", {"compute"})
            self.assertIn("x = 2", target.read_text(encoding="utf-8"))
            rollback_file(applied.rollback)
            self.assertEqual(target.read_text(encoding="utf-8"), SOURCE)

    def test_ablation_results_write_jsonl_and_csv(self) -> None:
        result = AblationResult(
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
            jsonl_path = write_ablation_jsonl(root / "patch_trials.jsonl", [result])
            csv_path = write_ablation_csv(root / "ablation_summary.csv", [result])
            record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["patch_ids"], ["patch-a"])
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["patch_ids"], "patch-a")


if __name__ == "__main__":
    unittest.main()
