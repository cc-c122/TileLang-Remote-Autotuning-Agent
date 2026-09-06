from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kernel_opt_agent.config_model import AppConfig
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.source_optimizer.analyzer import analyze_source, rewrite_copy_loop
from kernel_opt_agent.source_optimizer.engine import run_source_optimization
from kernel_opt_agent.source_optimizer.planner import choose_plan
from kernel_opt_agent.server.app import app


PAGED_SOURCE = Path("kernel_opt_agent/samples/paged_attention_decode/_upstream_sparse_gqa_decode_paged.py")


def _source() -> str:
    return '''from __future__ import annotations
import tilelang.language as TL

shape = [8]

def factory():
    @TL.prim_func
    def main(Input: TL.Tensor(shape, "float32"), Output: TL.Tensor(shape, "float32")):
        local = TL.alloc_fragment([8], "float32")
        for i in TL.Parallel(8):
            local[i] = Input[i]
        for i in TL.Parallel(8):
            Output[i] = local[i]
    return main
'''


def _config(sample: Path, **source_options: object) -> AppConfig:
    return AppConfig.model_validate(
        {
            "project_name": "source-test",
            "execution_mode": "source_optimization",
            "runner": {"type": "local"},
            "remote": {"auth_type": "key", "key_path": "~/.ssh/id_rsa"},
            "kernel": {
                "sample_path": str(sample),
                "entry_file": "kernel.py",
                "build_command": "build",
                "correctness_command": "correctness",
                "run_command": "benchmark",
            },
            "search": {"strategy": "rule_based", "max_iterations": 1, "candidates_per_iteration": 1, "timeout_seconds": 5},
            "search_space": {},
            "hardware_detection": {"enabled": False},
            "profiler": {"enabled": False, "type": "dummy"},
            "source_optimization": {
                "enabled": True,
                "max_candidates": 1,
                "benchmark_repeats": 2,
                "min_improvement_percent": 1.0,
                **source_options,
            },
        }
    )


class FakeRunner:
    def __init__(self, workspace: Path, mode: str = "accept"):
        self.workspace = workspace
        self.mode = mode

    def _result(self, name: str, command: str, code: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
        return CommandResult(name, command, code, stdout, stderr, 0.0, 0.1, 0.1)

    def run(self, name: str, command: str | None) -> CommandResult:
        command = command or ""
        entry = self.workspace / "kernel.py"
        changed = "TL.copy(" in entry.read_text(encoding="utf-8")
        if name.startswith("source_hash"):
            digest = hashlib.sha256(entry.read_bytes()).hexdigest()
            return self._result(name, command, stdout=f"SOURCE_SHA256={digest}\n")
        if name == "build" and changed and self.mode == "build_failed":
            return self._result(name, command, 1, stderr="fixture build failed")
        if name == "correctness":
            if changed and self.mode == "correctness_failed":
                return self._result(name, command, 1, 'CORRECTNESS_RESULT status=FAIL max_error=1 reason="wrong"\n')
            if changed and self.mode == "correctness_fallback":
                return self._result(name, command, stdout="passed\n")
            return self._result(name, command, stdout='CORRECTNESS_RESULT status=PASS max_error=0 reason="ok"\n')
        if name.startswith("benchmark"):
            if changed and self.mode == "benchmark_failed":
                return self._result(name, command, 1, stderr="fixture benchmark failed")
            if changed and self.mode == "protected_changed":
                (self.workspace / "correctness.py").write_text("changed", encoding="utf-8")
            latency = 12.0 if changed and self.mode == "regressed" else (5.0 if changed else 10.0)
            suffix = f" secret={os.environ.get('OPENAI_API_KEY', '')}" if self.mode == "secret_output" else ""
            return self._result(name, command, stdout=f"BENCHMARK_RESULT latency_ms={latency}{suffix}\n")
        return self._result(name, command)

    def close(self) -> None:
        return None


class InvalidPlanner:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages):
        self.calls += 1
        return {"target_id": "outside", "template": "arbitrary_code", "hypothesis": "bad", "evidence_ids": []}


class SourceAnalyzerTests(unittest.TestCase):
    def test_finds_nested_aliased_prim_func_and_rewrites_direct_copy(self) -> None:
        analysis = analyze_source(_source())
        self.assertIsNone(analysis.reason)
        self.assertEqual(len(analysis.targets), 1)
        self.assertEqual(analysis.targets[0].function_name, "factory.main")
        rewritten = rewrite_copy_loop(_source(), analysis.targets[0])
        self.assertIn("TL.copy(Input[0:8], local)", rewritten)

    def test_bounds_mismatch_and_side_effect_loop_are_not_authorized(self) -> None:
        mismatched = _source().replace("TL.Parallel(8)", "TL.Parallel(4)", 1)
        self.assertFalse(analyze_source(mismatched).targets)
        side_effect = _source().replace("local[i] = Input[i]", "local[i] = Input[i]\n            print(i)")
        self.assertFalse(analyze_source(side_effect).targets)
        already_bulk_copy = _source().replace(
            "for i in TL.Parallel(8):\n            local[i] = Input[i]",
            "TL.copy(Input[0:8], local)",
        )
        self.assertFalse(analyze_source(already_bulk_copy).targets)

    def test_paged_attention_regression_finds_only_output_partial_copy(self) -> None:
        analysis = analyze_source(PAGED_SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(len(analysis.targets), 1)
        target = analysis.targets[0]
        self.assertEqual(target.function_name, "flashattn.main")
        self.assertIn("Output_partial", target.source_expr)
        self.assertEqual(target.destination_expr, "po_local")

    def test_invalid_llm_selection_retries_once_then_uses_rule_fallback(self) -> None:
        analysis = analyze_source(_source())
        planner = InvalidPlanner()
        plan, source = choose_plan(analysis.targets, [], planner)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(source, "rule_based")
        self.assertEqual(plan.target_id, analysis.targets[0].target_id)


class SourceEngineTests(unittest.TestCase):
    def _run(self, mode: str, cancel=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        config = _config(sample)
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()

        def factory(config, trial_dir):
            return FakeRunner(trial_dir, mode)

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=factory):
            result = run_source_optimization(
                config,
                root / "workspace",
                results,
                analyze_source(entry.read_text(encoding="utf-8")),
                entry,
                should_cancel=cancel,
            )
        return root, results, result, original_hash

    def test_build_and_correctness_failures_rollback_original_hash(self) -> None:
        for mode, status in (
            ("build_failed", "build_failed"),
            ("correctness_failed", "correctness_failed"),
            ("correctness_fallback", "correctness_failed"),
            ("benchmark_failed", "benchmark_failed"),
        ):
            with self.subTest(mode=mode):
                root, _, result, original_hash = self._run(mode)
                trial = result.trials[0]
                self.assertEqual(trial.status, status)
                self.assertTrue(trial.rollback_verified)
                candidate = root / "workspace" / "source_trials" / trial.trial_id / "workspace" / "kernel.py"
                self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), original_hash)

    def test_regression_retains_baseline(self) -> None:
        _, _, result, original_hash = self._run("regressed")
        self.assertEqual(result.trials[0].status, "no_improvement")
        self.assertEqual(result.best_source_sha256, original_hash)
        self.assertIsNone(result.accepted_trial_id)

    def test_protected_file_change_rejects_and_rolls_back(self) -> None:
        _, _, result, _ = self._run("protected_changed")
        self.assertEqual(result.trials[0].status, "validation_failed")
        self.assertTrue(result.trials[0].rollback_verified)

    def test_command_logs_and_serialized_result_redact_configured_secrets(self) -> None:
        secret = "source-optimizer-test-secret-value"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            root, _, result, _ = self._run("secret_output")
        self.assertNotIn(secret, json.dumps(result.model_dump(mode="json")))
        trial_root = root / "workspace" / "source_trials" / result.trials[0].trial_id / "logs"
        self.assertNotIn(secret, "\n".join(path.read_text(encoding="utf-8") for path in trial_root.glob("*.log")))

    def test_cancel_stops_before_new_commands(self) -> None:
        calls = iter([False, True])
        _, _, result, _ = self._run("accept", cancel=lambda: next(calls, True))
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.trials, [])

    def test_accept_records_real_execution_hash_and_updates_best(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        package = sample / "tilelang"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "language.py").write_text(
            "def prim_func(fn): return fn\n"
            "def Tensor(*args): return list\n"
            "def alloc_fragment(shape, dtype): return [0] * shape[0]\n"
            "def Parallel(size): return range(size)\n"
            "def copy(source, destination): destination[:] = source\n",
            encoding="utf-8",
        )
        source = _source().replace("shape = [8]", "N = 200000\nshape = [N]").replace("[8]", "[N]").replace("TL.Parallel(8)", "TL.Parallel(N)")
        entry = sample / "kernel.py"
        entry.write_text(source, encoding="utf-8")
        (sample / "correctness.py").write_text(
            "from kernel import factory, N\n"
            "source = list(range(N)); output = [0] * N\n"
            "factory()(source, output)\n"
            "passed = output == source\n"
            "print(f'CORRECTNESS_RESULT status={\"PASS\" if passed else \"FAIL\"} max_error={0 if passed else 1} reason=\"fixture comparison\"')\n"
            "raise SystemExit(0 if passed else 1)\n",
            encoding="utf-8",
        )
        (sample / "benchmark.py").write_text(
            "import time\n"
            "from kernel import factory, N\n"
            "source = list(range(N)); output = [0] * N; kernel = factory()\n"
            "for _ in range(3): kernel(source, output)\n"
            "start = time.perf_counter(); kernel(source, output); elapsed = (time.perf_counter() - start) * 1000\n"
            "print(f'BENCHMARK_RESULT latency_ms={elapsed:.9f}')\n",
            encoding="utf-8",
        )
        config = _config(sample, benchmark_repeats=3)
        config.kernel.build_command = "python -m py_compile kernel.py"
        config.kernel.correctness_command = "python correctness.py"
        config.kernel.run_command = "python benchmark.py"
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
        result = run_source_optimization(
            config,
            root / "workspace",
            results,
            analyze_source(source),
            entry,
        )
        trial = result.trials[0]
        self.assertEqual(trial.status, "accepted", trial.model_dump())
        self.assertNotEqual(result.best_source_sha256, original_hash)
        self.assertEqual(trial.execution_source_sha256, trial.source_after_sha256)
        self.assertEqual(hashlib.sha256((results / "best_kernel.py").read_bytes()).hexdigest(), trial.source_after_sha256)


class SourceOptimizationApiTests(unittest.TestCase):
    def test_web_task_runs_controlled_source_optimization_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample"
            package = sample / "tilelang"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "language.py").write_text(
                "def prim_func(fn): return fn\n"
                "def Tensor(*args): return list\n"
                "def alloc_fragment(shape, dtype): return [0] * shape[0]\n"
                "def Parallel(size): return range(size)\n"
                "def copy(source, destination): destination[:] = source\n",
                encoding="utf-8",
            )
            source = _source().replace("shape = [8]", "N = 200000\nshape = [N]").replace("[8]", "[N]").replace("TL.Parallel(8)", "TL.Parallel(N)")
            (sample / "kernel.py").write_text(source, encoding="utf-8")
            (sample / "correctness.py").write_text(
                "from kernel import factory, N\n"
                "source = list(range(N)); output = [0] * N\n"
                "factory()(source, output); passed = output == source\n"
                "print(f'CORRECTNESS_RESULT status={\"PASS\" if passed else \"FAIL\"} max_error={0 if passed else 1} reason=\"fixture comparison\"')\n"
                "raise SystemExit(0 if passed else 1)\n",
                encoding="utf-8",
            )
            (sample / "benchmark.py").write_text(
                "import time\n"
                "from kernel import factory, N\n"
                "source = list(range(N)); output = [0] * N; kernel = factory()\n"
                "for _ in range(3): kernel(source, output)\n"
                "start = time.perf_counter(); kernel(source, output); elapsed = (time.perf_counter() - start) * 1000\n"
                "print(f'BENCHMARK_RESULT latency_ms={elapsed:.9f}')\n",
                encoding="utf-8",
            )
            client = TestClient(app)
            response = client.post(
                "/api/tasks",
                json={
                    "project_name": "source-api-mechanism",
                    "sample": {"source_type": "path", "path": str(sample), "entry_file": "kernel.py"},
                    "commands": {
                        "build_command": "python -m py_compile kernel.py",
                        "correctness_command": "python correctness.py",
                        "benchmark_command": "python benchmark.py",
                    },
                    "profiler": {"enabled": False, "type": "dummy"},
                    "optimization": {
                        "enabled": True,
                        "max_candidates": 1,
                        "benchmark_repeats": 3,
                        "min_improvement_percent": 1.0,
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            task_id = response.json()["task_id"]
            task = None
            for _ in range(160):
                task = client.get(f"/api/tasks/{task_id}").json()["task"]
                if task["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.1)
            self.assertEqual(task["status"], "completed", task)
            self.assertEqual(task["execution_mode"], "source_optimization")
            result = client.get(f"/api/tasks/{task_id}/results").json()["results"]
            source_result = result["source_optimization"]
            self.assertEqual(source_result["status"], "completed")
            self.assertEqual(source_result["trials"][0]["status"], "accepted")
            self.assertIn("TL.copy", result["best_kernel"])
            self.assertIn("## Source Optimization", result["report_markdown"])


if __name__ == "__main__":
    unittest.main()
