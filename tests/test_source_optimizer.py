from __future__ import annotations

import hashlib
import csv
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from kernel_opt_agent.config_model import AppConfig
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.source_optimizer.analyzer import analyze_source, rewrite_copy_loop
from kernel_opt_agent.source_optimizer.engine import run_source_optimization
from kernel_opt_agent.source_optimizer.planner import choose_plan
from kernel_opt_agent.server.app import _accepted_source_trial, _source_best_verification, app


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
            if changed and self.mode == "correctness_reason_secret":
                return self._result(name, command, stdout=f'CORRECTNESS_RESULT status=PASS max_error=0 reason="{os.environ.get("OPENAI_API_KEY", "")}"\n')
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
            if changed and self.mode == "entry_mutation":
                entry.write_text("# mutated after correctness\n", encoding="utf-8")
            latency = 12.0 if changed and self.mode == "regressed" else (5.0 if changed else 10.0)
            if changed and self.mode == "zero_latency":
                latency = 0.0
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


class RecordingPlanner:
    def __init__(self, response):
        self.response = response
        self.messages = []

    def chat_json(self, messages):
        self.messages.append(messages)
        return self.response


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

    def test_rejects_for_else_loop_dependent_prefix_and_rank_mismatch(self) -> None:
        loop_else = _source().replace(
            "local[i] = Input[i]",
            "local[i] = Input[i]\n        else:\n            preserve_side_effect()",
        )
        self.assertFalse(analyze_source(loop_else).targets)
        diagonal = _source().replace("shape = [8]", "shape = [8, 8]").replace("local[i] = Input[i]", "local[i] = Input[i, i]")
        self.assertFalse(analyze_source(diagonal).targets)
        rank_mismatch = _source().replace("shape = [8]", "shape = [8, 8]")
        self.assertFalse(analyze_source(rank_mismatch).targets)

    def test_rejects_dsl_shadowing_and_nested_non_prim_scope(self) -> None:
        shadowed = _source().replace(
            "def main(Input: TL.Tensor",
            "def main(Input: TL.Tensor",
        ).replace(
            "        local = TL.alloc_fragment",
            "        TL = object()\n        local = TL.alloc_fragment",
        )
        self.assertFalse(analyze_source(shadowed).targets)
        nested = _source().replace(
            "        local = TL.alloc_fragment([8], \"float32\")",
            "        def helper():\n            local = TL.alloc_fragment([8], \"float32\")\n            for i in TL.Parallel(8):\n                local[i] = Input[i]\n            return local\n        local = helper()",
        ).replace(
            "        for i in TL.Parallel(8):\n            local[i] = Input[i]\n",
            "",
            1,
        )
        self.assertFalse(analyze_source(nested).targets)

    def test_function_parameter_shadows_global_shape_binding(self) -> None:
        shadowed_shape = _source().replace("def factory():", "def factory(shape):")
        self.assertFalse(analyze_source(shadowed_shape).targets)

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
        self.assertEqual(plan.planning_attempts, 2)
        self.assertIn("ValueError", plan.fallback_reason or "")

    def test_llm_prompt_contains_target_summaries_and_evidence(self) -> None:
        analysis = analyze_source(_source())
        evidence = [{"evidence_id": "ev-1", "metric": "l2c_hit_rate", "value": 46.1, "available": True}]
        planner = RecordingPlanner(
            {"target_id": analysis.targets[0].target_id, "template": "parallel_copy_to_t_copy", "hypothesis": "bulk copy", "evidence_ids": ["ev-1"]}
        )
        plan, source = choose_plan(analysis.targets, evidence, planner)
        prompt = planner.messages[0][0]["content"]
        self.assertEqual(source, "llm")
        self.assertEqual(plan.evidence_ids, ["ev-1"])
        self.assertIn("l2c_hit_rate", prompt)
        self.assertIn(analysis.targets[0].source_expr, prompt)


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

    def test_entry_mutation_after_benchmark_is_rejected_and_best_stays_baseline(self) -> None:
        root, results, result, original_hash = self._run("entry_mutation")
        trial = result.trials[0]
        self.assertEqual(trial.status, "validation_failed")
        self.assertTrue(trial.rollback_verified)
        self.assertEqual(result.best_source_sha256, original_hash)
        self.assertEqual(hashlib.sha256((results / "best_kernel.py").read_bytes()).hexdigest(), original_hash)

    def test_remote_execution_workspace_is_restored_after_correctness_failure(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        remote = root / "remote"
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()

        def factory(config, trial_dir):
            remote.mkdir(exist_ok=True)
            shutil.copytree(trial_dir, remote, dirs_exist_ok=True)
            return FakeRunner(remote, "correctness_failed")

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=factory):
            result = run_source_optimization(_config(sample), root / "workspace", results, analyze_source(_source()), entry)
        self.assertEqual(result.trials[0].status, "correctness_failed")
        self.assertTrue(result.trials[0].rollback_verified)
        self.assertEqual(hashlib.sha256((remote / "kernel.py").read_bytes()).hexdigest(), original_hash)

    def test_rollback_failure_is_explicit_and_never_accepts(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")

        class FailingRestoreRunner(FakeRunner):
            def upload(self, path):
                raise RuntimeError("restore fixture failed")

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=lambda config, trial_dir: FailingRestoreRunner(trial_dir)):
            result = run_source_optimization(_config(sample), root / "workspace", results, analyze_source(_source()), entry)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.trials[0].status, "validation_failed")
        self.assertFalse(result.trials[0].rollback_verified)
        self.assertIn("rollback failed", result.trials[0].decision_reason or "")

    def test_cancel_after_measurement_restores_baseline_and_does_not_publish(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        state = {"cancel": False}

        class CancelRunner(FakeRunner):
            def run(self, name, command):
                result = super().run(name, command)
                if name.startswith("benchmark") and "TL.copy(" in (self.workspace / "kernel.py").read_text(encoding="utf-8"):
                    state["cancel"] = True
                return result

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=lambda config, trial_dir: CancelRunner(trial_dir)):
            result = run_source_optimization(
                _config(sample, benchmark_repeats=1),
                root / "workspace",
                results,
                analyze_source(_source()),
                entry,
                should_cancel=lambda: state["cancel"],
            )
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.accepted_trial_id)
        self.assertEqual(result.trials[0].status, "cancelled")
        self.assertTrue(result.trials[0].rollback_verified)
        self.assertEqual(hashlib.sha256((results / "best_kernel.py").read_bytes()).hexdigest(), original_hash)

    def test_publish_artifact_mutation_restores_baseline(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        workspace = root / "workspace"

        def factory(config, trial_dir):
            runner = FakeRunner(trial_dir)
            if trial_dir.parent.name == "accepted":
                source_after = next((workspace / "source_trials").glob("source-*/source_after.py"))
                source_after.write_text("# tampered publication artifact\n", encoding="utf-8")
            return runner

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=factory):
            result = run_source_optimization(_config(sample), workspace, results, analyze_source(_source()), entry)
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.accepted_trial_id)
        self.assertEqual(result.trials[0].status, "validation_failed")
        self.assertTrue(result.trials[0].rollback_verified)
        self.assertEqual(hashlib.sha256((results / "best_kernel.py").read_bytes()).hexdigest(), original_hash)

    def test_partial_publish_runner_failure_reconnects_and_restores(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        remote = root / "remote"
        calls = {"publish_failed": False}

        def factory(config, trial_dir):
            if trial_dir.parent.name == "accepted":
                remote.mkdir(exist_ok=True)
                shutil.copytree(trial_dir, remote, dirs_exist_ok=True)
                calls["publish_failed"] = True
                raise RuntimeError("fixture failed after partial deploy")
            if trial_dir.name == "rollback_snapshot" and calls["publish_failed"]:
                shutil.copytree(trial_dir, remote, dirs_exist_ok=True)
                return FakeRunner(remote)
            return FakeRunner(trial_dir)

        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=factory):
            result = run_source_optimization(_config(sample), root / "workspace", results, analyze_source(_source()), entry)
        original_hash = hashlib.sha256(entry.read_bytes()).hexdigest()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.trials[0].status, "validation_failed")
        self.assertTrue(result.trials[0].rollback_verified)
        self.assertEqual(hashlib.sha256((remote / "kernel.py").read_bytes()).hexdigest(), original_hash)

    def test_snapshot_and_diff_exist_before_candidate_runner_starts(self) -> None:
        checks = []

        def factory(config, trial_dir):
            if trial_dir.parent.name.startswith("source-") and "TL.copy(" in (trial_dir / "kernel.py").read_text(encoding="utf-8"):
                root = trial_dir.parent
                checks.append(
                    (root / "rollback_snapshot" / "kernel.py").is_file()
                    and "TL.copy(" not in (root / "rollback_snapshot" / "kernel.py").read_text(encoding="utf-8")
                    and (root / "source_before.py").is_file()
                    and (root / "source_after.py").is_file()
                    and (root / "candidate.diff").is_file()
                )
            return FakeRunner(trial_dir)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(_source(), encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=factory):
            run_source_optimization(_config(sample), root / "workspace", results, analyze_source(_source()), entry)
        self.assertEqual(checks, [True])

    def test_zero_latency_is_rejected(self) -> None:
        _, _, result, _ = self._run("zero_latency")
        self.assertEqual(result.trials[0].status, "benchmark_failed")

    def test_command_logs_and_serialized_result_redact_configured_secrets(self) -> None:
        secret = "source-optimizer-test-secret-value"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            root, _, result, _ = self._run("secret_output")
        self.assertNotIn(secret, json.dumps(result.model_dump(mode="json")))
        trial_root = root / "workspace" / "source_trials" / result.trials[0].trial_id / "logs"
        self.assertNotIn(secret, "\n".join(path.read_text(encoding="utf-8") for path in trial_root.glob("*.log")))

    def test_correctness_reason_and_all_serialized_outputs_redact_secret(self) -> None:
        secret = "source-correctness-secret-value"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            root, results, result, _ = self._run("correctness_reason_secret")
        self.assertNotIn(secret, json.dumps(result.model_dump(mode="json")))
        for path in [results / "source_optimization.json", results / "experiments.jsonl", results / "summary.csv", results / "all_results.csv", results / "report.md"]:
            self.assertNotIn(secret, path.read_text(encoding="utf-8"), str(path))

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
        self.assertIsNone(trial.rollback_verified)
        self.assertEqual(len(trial.baseline_latency_ms), 3)
        self.assertEqual(len(trial.candidate_latency_ms), 3)
        self.assertEqual(trial.baseline_median_latency_ms, sorted(trial.baseline_latency_ms)[1])
        self.assertEqual(trial.candidate_median_latency_ms, sorted(trial.candidate_latency_ms)[1])
        self.assertEqual(hashlib.sha256((results / "best_kernel.py").read_bytes()).hexdigest(), trial.source_after_sha256)
        best_config = yaml.safe_load((results / "best_config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(best_config["source_optimization_result"]["source_sha256"], trial.source_after_sha256)
        experiments = [json.loads(line) for line in (results / "experiments.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(experiments[-1]["status"], "benchmark_ok")
        self.assertEqual(experiments[-1]["config_hash"], trial.source_after_sha256)
        with (results / "summary.csv").open(newline="", encoding="utf-8") as handle:
            summary = list(csv.DictReader(handle))
        self.assertEqual(summary[-1]["config_hash"], trial.source_after_sha256)
        self.assertIn(str(trial.candidate_median_latency_ms), (results / "report.md").read_text(encoding="utf-8"))

    def test_max_candidates_runs_multiple_authorized_targets(self) -> None:
        source = _source().replace(
            "        for i in TL.Parallel(8):\n            Output[i] = local[i]",
            "        local_two = TL.alloc_fragment([8], \"float32\")\n        for i in TL.Parallel(8):\n            local_two[i] = Input[i]\n        for i in TL.Parallel(8):\n            Output[i] = local_two[i]",
        )
        analysis = analyze_source(source)
        self.assertEqual(len(analysis.targets), 2)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(source, encoding="utf-8")
        (sample / "correctness.py").write_text("protected", encoding="utf-8")
        results = root / "results"
        results.mkdir()
        (results / "metric_observations.jsonl").write_text("", encoding="utf-8")
        with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=lambda config, trial_dir: FakeRunner(trial_dir)):
            result = run_source_optimization(_config(sample, max_candidates=2), root / "workspace", results, analysis, entry)
        self.assertEqual(len(result.trials), 2)


class SourceOptimizationApiTests(unittest.TestCase):
    def test_accepted_source_summary_requires_status_correctness_and_hash_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            best_kernel = results / "best_kernel.py"
            best_kernel.write_text("accepted source\n", encoding="utf-8")
            digest = hashlib.sha256(best_kernel.read_bytes()).hexdigest()
            source_result = {
                "status": "completed",
                "baseline_verified": True,
                "best_source_sha256": digest,
                "accepted_trial_id": "source-1",
                "trials": [
                    {
                        "trial_id": "source-1",
                        "status": "accepted",
                        "correctness": {"passed": True},
                        "source_after_sha256": digest,
                        "execution_source_sha256": digest,
                    }
                ],
            }
            self.assertIsNotNone(_accepted_source_trial(results, source_result))
            baseline_result = {
                "status": "completed",
                "baseline_verified": True,
                "baseline_source_sha256": digest,
                "best_source_sha256": digest,
                "accepted_trial_id": None,
                "trials": [],
            }
            self.assertEqual(_source_best_verification(results, baseline_result), (None, True, None))
            for field, value in [("status", "no_improvement"), ("correctness", {"passed": False}), ("source_after_sha256", "0" * 64)]:
                invalid = json.loads(json.dumps(source_result))
                invalid["trials"][0][field] = value
                with self.subTest(field=field):
                    self.assertIsNone(_accepted_source_trial(results, invalid))

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
                "import os\n"
                "from kernel import factory, N\n"
                "source = list(range(N)); output = [0] * N\n"
                "factory()(source, output); passed = output == source\n"
                "reason = os.environ.get('OPENAI_API_KEY', 'fixture comparison')\n"
                "print(f'CORRECTNESS_RESULT status={\"PASS\" if passed else \"FAIL\"} max_error={0 if passed else 1} reason=\"{reason}\"')\n"
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
            target_id = analyze_source(source).targets[0].target_id
            planner = RecordingPlanner(
                {"target_id": target_id, "template": "parallel_copy_to_t_copy", "hypothesis": "use verified bulk copy", "evidence_ids": []}
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-api-key"}), patch(
                "kernel_opt_agent.server.job_worker.OpenAICompatibleClient",
                return_value=planner,
            ) as client_factory:
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
                self.assertTrue(client_factory.called)
                self.assertTrue(planner.messages)
            self.assertEqual(task["status"], "completed", task)
            self.assertEqual(task["execution_mode"], "source_optimization")
            result = client.get(f"/api/tasks/{task_id}/results").json()["results"]
            source_result = result["source_optimization"]
            self.assertEqual(source_result["status"], "completed")
            self.assertEqual(source_result["trials"][0]["status"], "accepted")
            accepted = source_result["trials"][0]
            self.assertIsNone(accepted["rollback_verified"])
            summary_path = Path(task["results_dir"]) / "summary.csv"
            with summary_path.open(newline="", encoding="utf-8") as handle:
                summary_rows = list(csv.DictReader(handle))
                fieldnames = list(summary_rows[0])
            summary_rows[0]["latency"] = "999.0"
            summary_rows[0]["objective_value"] = "999.0"
            with summary_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(summary_rows)
            task = client.get(f"/api/tasks/{task_id}").json()["task"]
            self.assertEqual(result["best_row"]["config_hash"], accepted["source_after_sha256"])
            self.assertEqual(float(result["best_row"]["latency"]), accepted["candidate_median_latency_ms"])
            self.assertEqual(result["best_config"]["source_optimization_result"]["source_sha256"], accepted["source_after_sha256"])
            self.assertEqual(task["best_latency"], accepted["candidate_median_latency_ms"])
            self.assertEqual(task["baseline_latency"], accepted["baseline_median_latency_ms"])
            self.assertNotEqual(task["baseline_latency"], 999.0)
            self.assertEqual(task["improvement_percent"], accepted["improvement_percent"])
            self.assertTrue(task["source_best_verified"])
            self.assertIsNone(task["source_best_verification_error"])
            self.assertTrue(result["source_best_verified"])
            self.assertIsNone(result["source_best_verification_error"])
            self.assertGreaterEqual(task["total_trials"], 2)
            event_types = [item["type"] for item in task["events"]]
            self.assertIn("source_trial_started", event_types)
            self.assertIn("source_trial_completed", event_types)
            self.assertIn("TL.copy", result["best_kernel"])
            self.assertIn("## Source Optimization", result["report_markdown"])
            self.assertIn("## Authoritative Source Result", result["report_markdown"])
            self.assertIn("## Pre-source Best Seen", result["report_markdown"])
            self.assertLess(result["report_markdown"].index("## Authoritative Source Result"), result["report_markdown"].index("## Pre-source Best Seen"))
            self.assertIn(accepted["source_after_sha256"], result["report_markdown"])
            self.assertIs(result["summary_table"][0]["correctness"]["passed"], True)
            public_payload = json.dumps({"task": task, "results": result}, ensure_ascii=False)
            self.assertNotIn("fixture-api-key", public_payload)
            for path in Path(task["results_dir"]).rglob("*"):
                if path.is_file():
                    self.assertNotIn("fixture-api-key", path.read_text(encoding="utf-8", errors="ignore"), str(path))
            download = client.get(f"/api/tasks/{task_id}/download/best_kernel")
            self.assertEqual(download.status_code, 200)

            results_root = Path(task["results_dir"])
            best_path = results_root / "best_kernel.py"
            source_result_path = results_root / "source_optimization.json"
            original_best = best_path.read_bytes()
            original_source_result = json.loads(source_result_path.read_text(encoding="utf-8"))
            for failure in ["best_modified", "best_missing", "execution_hash", "baseline_unverified"]:
                best_path.write_bytes(original_best)
                current_source_result = json.loads(json.dumps(original_source_result))
                if failure == "best_modified":
                    best_path.write_text("tampered\n", encoding="utf-8")
                elif failure == "best_missing":
                    best_path.unlink()
                elif failure == "execution_hash":
                    current_source_result["trials"][0]["execution_source_sha256"] = "0" * 64
                else:
                    current_source_result["baseline_verified"] = False
                source_result_path.write_text(json.dumps(current_source_result), encoding="utf-8")
                with self.subTest(failure=failure):
                    invalid_task = client.get(f"/api/tasks/{task_id}").json()["task"]
                    invalid_result = client.get(f"/api/tasks/{task_id}/results").json()["results"]
                    self.assertFalse(invalid_task["source_best_verified"])
                    self.assertIsNotNone(invalid_task["source_best_verification_error"])
                    self.assertIsNone(invalid_task["best_latency"])
                    self.assertIsNone(invalid_task["improvement_percent"])
                    self.assertFalse(invalid_result["source_best_verified"])
                    self.assertIsNotNone(invalid_result["source_best_verification_error"])
                    self.assertIsNone(invalid_result["best_kernel"])
                    self.assertIsNone(invalid_result["best_config"])
                    self.assertIsNone(invalid_result["best_row"])
                    self.assertIsNone(invalid_result["improvement_percent"])
                    self.assertEqual(invalid_result["source_optimization"]["accepted_trial_id"], accepted["trial_id"])
                    self.assertTrue(invalid_result["summary_table"])
                    self.assertIsNotNone(invalid_result["report_markdown"])
                    self.assertEqual(client.get(f"/api/tasks/{task_id}/download/best_kernel").status_code, 409)
            best_path.write_bytes(original_best)
            source_result_path.write_text(json.dumps(original_source_result), encoding="utf-8")

    def test_failed_source_baseline_is_not_downloadable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sample = Path(temporary) / "sample"
            sample.mkdir()
            (sample / "kernel.py").write_text(_source(), encoding="utf-8")
            (sample / "correctness.py").write_text('print("CORRECTNESS_RESULT status=PASS max_error=0 reason=ok")\n', encoding="utf-8")
            (sample / "benchmark.py").write_text('print("BENCHMARK_RESULT latency_ms=1")\n', encoding="utf-8")
            client = TestClient(app)
            response = client.post(
                "/api/tasks",
                json={
                    "project_name": "source-baseline-failure",
                    "sample": {"source_type": "path", "path": str(sample), "entry_file": "kernel.py"},
                    "commands": {
                        "build_command": "python -c \"raise SystemExit(1)\"",
                        "correctness_command": "python correctness.py",
                        "benchmark_command": "python benchmark.py",
                    },
                    "profiler": {"enabled": False, "type": "dummy"},
                    "optimization": {"enabled": True, "max_candidates": 1, "benchmark_repeats": 1, "min_improvement_percent": 1.0},
                },
            )
            task_id = response.json()["task_id"]
            task = None
            for _ in range(100):
                task = client.get(f"/api/tasks/{task_id}").json()["task"]
                if task["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.1)
            result = client.get(f"/api/tasks/{task_id}/results").json()["results"]
            self.assertFalse(result["source_optimization"]["baseline_verified"])
            self.assertFalse(result["source_best_verified"])
            self.assertIsNone(result["best_kernel"])
            self.assertIsNone(result["best_row"])
            self.assertIsNone(result["improvement_percent"])
            task = client.get(f"/api/tasks/{task_id}").json()["task"]
            self.assertFalse(task["source_best_verified"])
            self.assertIsNone(task["best_latency"])
            self.assertIsNone(task["improvement_percent"])
            download = client.get(f"/api/tasks/{task_id}/download/best_kernel")
            self.assertEqual(download.status_code, 409)


if __name__ == "__main__":
    unittest.main()
