from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.main import RESULTS_DIR, run
from kernel_opt_agent.run_request import RunRequest, app_config_from_run_request


KERNEL_WITH_COMPUTE = """BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}

def kernel():
    # BEGIN_AGENT_PATCH: compute
    value = BM + BN + BK + NUM_THREADS + NUM_STAGES + VECTOR_WIDTH
    # END_AGENT_PATCH
    return value
"""

KERNEL_WITH_OTHER_REGION = KERNEL_WITH_COMPUTE.replace("BEGIN_AGENT_PATCH: compute", "BEGIN_AGENT_PATCH: other")


def _write_sample(root: Path, kernel_text: str, fail_on_patch: bool = False) -> Path:
    sample = root / "sample"
    sample.mkdir()
    (sample / "kernel.py").write_text(kernel_text, encoding="utf-8")
    correctness = """
import sys
from pathlib import Path

text = Path("kernel.py").read_text(encoding="utf-8")
if {fail_on_patch!r} and "controlled_patch_trial" in text:
    print('CORRECTNESS_RESULT status=FAIL max_error=1 reason="patch failed"')
    sys.exit(1)
print('CORRECTNESS_RESULT status=PASS max_error=0 reason="ok"')
""".format(fail_on_patch=fail_on_patch)
    (sample / "correctness.py").write_text(correctness, encoding="utf-8")
    (sample / "benchmark.py").write_text(
        "print('BENCHMARK_RESULT latency_ms=5.0 tflops=1.0 bandwidth_gbps=2.0')\n",
        encoding="utf-8",
    )
    return sample


def _write_config(root: Path, sample: Path, patching: dict[str, object] | None) -> Path:
    config = {
        "project_name": "controlled-patch-test",
        "runner": {"type": "local"},
        "remote": {"auth_type": "key", "key_path": "~/.ssh/id_rsa"},
        "kernel": {
            "sample_path": str(sample),
            "entry_file": "kernel.py",
            "build_command": "python -m py_compile kernel.py",
            "correctness_command": "python correctness.py",
            "run_command": "python benchmark.py",
        },
        "search": {
            "strategy": "rule_based",
            "max_iterations": 1,
            "candidates_per_iteration": 1,
            "timeout_seconds": 20,
            "objective": "latency",
        },
        "search_space": {
            "BM": [1],
            "BN": [1],
            "BK": [1],
            "NUM_THREADS": [1],
            "NUM_STAGES": [1],
            "VECTOR_WIDTH": [1],
        },
        "hardware_detection": {"builtin_profile": False, "safe_probe": False},
        "profiler": {"enabled": False, "type": "dummy"},
    }
    if patching is not None:
        config["patching"] = patching
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _patch_trials() -> list[dict[str, object]]:
    path = RESULTS_DIR / "patch_trials.jsonl"
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class ControlledPatchFlowTests(unittest.TestCase):
    def test_patching_disabled_leaves_patch_trials_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = _write_sample(root, KERNEL_WITH_COMPUTE)
            config = load_config(_write_config(root, sample, {"enabled": False, "run_controlled_trial": False}))
            run(config)
            self.assertEqual(_patch_trials(), [])

    def test_run_request_omitted_patching_does_not_generate_patch_trial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = _write_sample(root, KERNEL_WITH_COMPUTE)
            request = RunRequest.model_validate(
                {
                    "schema_version": "v2.run_request.v1",
                    "project_name": "controlled-patch-request",
                    "sample": {
                        "source_type": "path",
                        "inline_text": None,
                        "path": str(sample),
                        "entry_file": "kernel.py",
                    },
                    "commands": {
                        "build_command": "python -m py_compile kernel.py",
                        "correctness_command": "python correctness.py",
                        "benchmark_command": "python benchmark.py",
                    },
                    "target": {"gpu_model": "unknown", "backend": "unknown"},
                    "settings_ref": {"use_saved_settings": False},
                    "hardware_overrides": {"fields": {}},
                    "search": {
                        "strategy": "rule_based",
                        "max_iterations": 1,
                        "candidates_per_iteration": 1,
                        "timeout_seconds": 20,
                        "objective": "latency",
                    },
                    "profiler": {"enabled": False, "type": "dummy"},
                }
            )
            config = app_config_from_run_request(request)
            self.assertFalse(config.patching.enabled)
            self.assertFalse(config.patching.run_controlled_trial)
            run(config)
            self.assertEqual(_patch_trials(), [])

    def test_enabled_without_compute_region_records_validation_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = _write_sample(root, KERNEL_WITH_OTHER_REGION)
            config = load_config(_write_config(root, sample, {"enabled": True, "run_controlled_trial": True}))
            run(config)
            trials = _patch_trials()
            self.assertEqual(len(trials), 1)
            self.assertEqual(trials[0]["status"], "validation_failed")
            self.assertIn("target_region is not present", trials[0]["error"])

    def test_enabled_with_region_records_trial_and_does_not_affect_best_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = _write_sample(root, KERNEL_WITH_COMPUTE)
            config = load_config(_write_config(root, sample, {"enabled": True, "run_controlled_trial": True}))
            run(config)
            trials = _patch_trials()
            self.assertEqual(len(trials), 1)
            self.assertEqual(trials[0]["status"], "benchmark_ok")
            self.assertEqual(trials[0]["metrics_after"]["latency_ms"], 5.0)
            self.assertTrue(trials[0]["artifacts"]["rollback"]["verified"])
            self.assertNotIn("controlled_patch_trial", (RESULTS_DIR / "best_kernel.py").read_text(encoding="utf-8"))
            report = (RESULTS_DIR / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Patch Trials", report)
            self.assertIn("benchmark_ok", report)

    def test_patch_correctness_failure_rolls_back_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = _write_sample(root, KERNEL_WITH_COMPUTE, fail_on_patch=True)
            config = load_config(_write_config(root, sample, {"enabled": True, "run_controlled_trial": True}))
            run(config)
            trials = _patch_trials()
            self.assertEqual(len(trials), 1)
            self.assertEqual(trials[0]["status"], "correctness_failed")
            self.assertTrue(trials[0]["artifacts"]["rollback"]["verified"])
            self.assertNotIn("controlled_patch_trial", (RESULTS_DIR / "best_kernel.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
