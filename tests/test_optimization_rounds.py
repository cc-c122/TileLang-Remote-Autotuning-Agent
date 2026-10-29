"""Synthetic no-GPU feedback/gating tests; timings are test data, not performance evidence."""
from __future__ import annotations

import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.source_optimizer.analyzer import analyze_source
from kernel_opt_agent.source_optimizer.engine import run_source_optimization
from kernel_opt_agent.source_optimizer.planner import choose_plan
from kernel_opt_agent.source_optimizer.rounds import bound_evidence, read_rows
from tests.test_source_optimizer import FakeRunner, _config, _source


engine = importlib.import_module("kernel_opt_agent.source_optimizer.engine")
server = importlib.import_module("kernel_opt_agent.server.app")


def two_copies():
    return _source().replace(
        "        for i in TL.Parallel(8):\n            Output[i] = local[i]",
        "        other = TL.alloc_fragment([8], \"float32\")\n"
        "        for j in TL.Parallel(8):\n            other[j] = Input[j]\n"
        "        for i in TL.Parallel(8):\n            Output[i] = local[i]",
    )


class FeedbackPlanner:
    def __init__(self, behavior="attempt"):
        self.behavior = behavior
        self.contexts = []

    def chat_json(self, messages):
        text = messages[0]["content"].split("\nPLANNING_CONTEXT_JSON:\n", 1)[1]
        payload = json.JSONDecoder().raw_decode(text)[0]
        self.contexts.append(payload)
        targets = payload["authorized_targets"]
        decision = "attempt" if targets and self.behavior not in {"stop", "insufficient_evidence"} else self.behavior if targets else "stop"
        target = targets[0] if decision == "attempt" else None
        return {
            "decision": decision, "bottleneck_type": "source_structure", "confidence": "low",
            "evidence_ids": payload["authorized_evidence_ids"],
            "target_id": target["target_id"] if target else None,
            "template": "not_implemented_transform" if self.behavior == "unsupported" else target["template"] if target else None,
            "hypothesis": "LLM conclusion for round " + str(payload["feedback"]["round_index"]),
            "expected_effect": "unmeasured change in copy lowering", "risk": "may regress or fail compilation",
            "validation_plan": ["fixed-reference correctness", "independent repeats and existing noise gate"],
        }


class FeedbackRunner(FakeRunner):
    def __init__(self, workspace, calls, mode):
        super().__init__(workspace)
        self.calls, self.feedback_mode = calls, mode

    def run(self, name, command):
        source = (self.workspace / "kernel.py").read_text(encoding="utf-8")
        copies = source.count("TL.copy(")
        self.calls.append((self.workspace, name, copies))
        if name == "build":
            return self._result(name, command, stdout="TEST_DATA: synthetic codegen and timing\nGENERATED_SOURCE_SHA256=" + hashlib.sha256(source.encode()).hexdigest())
        if name == "correctness" and self.feedback_mode == "fail_first" and "TL.copy(Input[0:8], local)" in source:
            return self._result(name, command, 1, 'CORRECTNESS_RESULT status=FAIL max_error=0.5 reason="synthetic candidate mismatch"')
        if name == "correctness" and self.feedback_mode == "baseline_failed":
            return self._result(name, command, 1, 'CORRECTNESS_RESULT status=FAIL max_error=0.25 reason="synthetic baseline mismatch"')
        if name.startswith("benchmark"):
            regressed = self.feedback_mode == "regress_first" and "TL.copy(Input[0:8], local)" in source
            latency = 12 if regressed else {0: 10, 1: 7, 2: 4}[copies]
            return self._result(name, command, stdout=f"TEST_DATA: synthetic latency, not GPU measurement\nBENCHMARK_RESULT latency_ms={latency}")
        return super().run(name, command)


class OptimizationRoundTests(unittest.TestCase):
    def test_explicit_profiler_server_path_reaches_internal_config(self):
        from kernel_opt_agent.server.models import TaskCreateRequest
        from kernel_opt_agent.server.run_request_builder import build_effective_config

        request = TaskCreateRequest.model_validate({
            "sample": {"source_type": "inline", "inline_text": "value = 1\n", "entry_file": "kernel.py"},
            "runner": {"type": "local"},
            "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": False,
                         "mcprofiler_executable": "/tmp/managed-tools/mcProfiler",
                         "mcprofiler_server_executable": "/tmp/managed-tools/profiler_server"},
        })
        with tempfile.TemporaryDirectory() as directory:
            config = build_effective_config(request, Path(directory))
            self.assertEqual(config.profiler.mcprofiler_executable, "/tmp/managed-tools/mcProfiler")
            self.assertEqual(config.profiler.mcprofiler_server_executable, "/tmp/managed-tools/profiler_server")

    def run_fixture(self, mode="accept", planner=None, budget=2, profiler=False, rewrite=None, collector=None, cancel=None, on_event=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        sample = root / "sample"
        sample.mkdir()
        entry = sample / "kernel.py"
        entry.write_text(two_copies(), encoding="utf-8")
        (sample / "correctness.py").write_text("# protected test data", encoding="utf-8")
        config = _config(sample, max_candidates=budget)
        config.profiler.enabled = profiler
        config.profiler.type = "mxmaca"
        config.profiler.auto_collect = profiler
        calls = []
        client = planner or FeedbackPlanner()
        contexts = [patch.object(engine, "build_runner", side_effect=lambda cfg, directory: FeedbackRunner(directory, calls, mode))]
        if rewrite is not None:
            contexts.append(patch.object(engine, "rewrite_source_target", side_effect=rewrite))
        if collector is not None:
            contexts.append(patch.object(engine, "collect_remote_mcprofiler_case", side_effect=collector))
        for context in contexts:
            context.start()
        try:
            result = run_source_optimization(config, root / "workspace", root / "results", analyze_source(two_copies()), entry,
                                             planning_client=client, should_cancel=cancel, event_callback=on_event)
        finally:
            for context in reversed(contexts):
                context.stop()
        return root, result, client, calls, read_rows(root / "results/optimization_rounds.jsonl")

    def test_success_replans_from_new_best_and_recomputes_target_ids(self):
        root, result, planner, calls, rounds = self.run_fixture()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stop_reason, "budget_exhausted")
        self.assertEqual(len(planner.contexts), 2)
        first, second = result.trials
        self.assertEqual([first.status, second.status], ["accepted", "accepted"])
        self.assertEqual(second.source_before_sha256, first.source_after_sha256)
        self.assertEqual(second.baseline_latency_ms, [7, 7, 7])
        second_context = planner.contexts[1]["feedback"]
        self.assertEqual(second_context["current_best"]["source_sha256"], first.source_after_sha256)
        self.assertEqual(second_context["history"][0]["status"], "accepted")
        self.assertEqual(second_context["remaining_budget"], 1)
        old_ids = {item["target_id"] for item in planner.contexts[0]["authorized_targets"]}
        self.assertNotIn(planner.contexts[1]["authorized_targets"][0]["target_id"], old_ids)
        self.assertEqual(second.artifacts["baseline_recheck"]["source_sha256"], first.source_after_sha256)
        self.assertEqual(result.best_source_sha256, second.source_after_sha256)
        self.assertEqual((root / "results/best_kernel.py").read_text().count("TL.copy("), 2)
        self.assertEqual(first.hypothesis, "LLM conclusion for round 1")
        self.assertNotEqual(first.hypothesis, first.artifacts["template_description"])
        payload = server._result_payload(root / "results", "source_optimization")
        self.assertEqual(payload["improvement_percent"], 60)
        self.assertEqual(payload["optimization_rounds"], rounds)
        self.assertEqual([row["round_index"] for row in rounds], [0, 1, 2])
        self.assertTrue(all(trial.artifacts["performance_gate"]["baseline_recheck_passed"] for trial in result.trials))
        broken = result.model_dump(mode="json")
        broken["trials"][0]["status"] = "correctness_failed"
        self.assertFalse(server._source_best_verification(root / "results", broken)[1])
        self.assertIn("60.000%", (root / "results/report.md").read_text())

    def test_baseline_failure_prevents_planning_benchmark_and_profiling(self):
        _, result, planner, calls, rounds = self.run_fixture("baseline_failed", profiler=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.baseline["correctness"]["max_error"], 0.25)
        self.assertEqual(result.trials, [])
        self.assertEqual(planner.contexts, [])
        self.assertFalse(any(name.startswith("benchmark") for _, name, _ in calls))
        self.assertEqual(rounds[0]["acceptance"]["decision"], "failed")

    def test_correctness_failure_is_feedback_not_a_new_parent(self):
        root, result, planner, calls, rounds = self.run_fixture("fail_first")
        self.assertEqual([trial.status for trial in result.trials], ["correctness_failed", "accepted"])
        first, second = result.trials
        self.assertEqual(second.source_before_sha256, result.baseline_source_sha256)
        self.assertTrue(first.rollback_verified)
        self.assertEqual(first.candidate_latency_ms, [])
        self.assertFalse(any(name.startswith("benchmark") for path, name, _ in calls if path.parent.name == first.trial_id))
        self.assertEqual(planner.contexts[1]["feedback"]["history"][0]["correctness"]["max_error"], 0.5)
        self.assertEqual(rounds[1]["acceptance"]["decision"], "correctness_failed")
        self.assertNotIn("TL.copy(Input[0:8], local)", (root / "results/best_kernel.py").read_text())

    def test_regressed_candidate_rolls_back_and_next_round_sees_failure(self):
        _, result, planner, _, rounds = self.run_fixture("regress_first")
        first, second = result.trials
        self.assertEqual(first.status, "no_improvement")
        self.assertTrue(first.rollback_verified)
        self.assertEqual(first.artifacts["performance_gate"]["decision"], "no_improvement")
        self.assertEqual(second.source_before_sha256, result.baseline_source_sha256)
        self.assertEqual(planner.contexts[1]["feedback"]["history"][0]["status"], "no_improvement")
        self.assertEqual(rounds[1]["validation"]["benchmark_samples_ms"], [12, 12, 12])

    def test_profiler_failure_is_not_a_correctness_failure_or_a_gain(self):
        _, result, _, calls, rounds = self.run_fixture(profiler=True)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.trials), 2)
        for row in rounds[1:]:
            self.assertEqual(row["validation"]["profiler_collection"]["status"], "unsupported")
            self.assertEqual(row["analysis"]["basis"], "source_and_logs_only")
            self.assertEqual(row["analysis"]["confidence"], "low")
            self.assertEqual(row["validation"]["benchmark_samples_ms"], row["validation"]["performance_gate"]["candidate_samples_ms"])

    def test_stop_and_insufficient_evidence_never_make_unrelated_changes(self):
        for behavior in ("stop", "insufficient_evidence", "unsupported"):
            with self.subTest(behavior=behavior):
                root, result, planner, _, rounds = self.run_fixture(planner=FeedbackPlanner(behavior))
                self.assertEqual(result.trials, [])
                self.assertEqual(result.best_source_sha256, result.baseline_source_sha256)
                self.assertEqual(len(planner.contexts), 1)
                self.assertIsNone(rounds[1]["candidate_source_sha256"])
                self.assertEqual(rounds[1]["diff"], "")
                if behavior == "unsupported":
                    self.assertIn("unsupported_transformation", result.stop_reason)

    def test_duplicate_source_is_not_executed_twice(self):
        original_rewrite = engine.rewrite_source_target
        first_version = []

        def repeat_first(source, target):
            if not first_version:
                first_version.append(original_rewrite(source, target))
            return first_version[0]

        _, result, _, calls, rounds = self.run_fixture(rewrite=repeat_first)
        self.assertEqual([trial.status for trial in result.trials], ["accepted", "validation_failed"])
        self.assertIn("duplicate_source", result.trials[1].decision_reason)
        self.assertFalse(any(path.parent.name == result.trials[1].trial_id for path, _, _ in calls))
        self.assertEqual(rounds[2]["validation"]["status"], "validation_failed")

    def test_real_case_is_deterministic_and_still_ineligible_for_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = Path("tests/fixtures/mcprofiler/real_v8_tc1_gate")
            import_report(case, root, "source-baseline")
            first = (root / "metric_observations.jsonl").read_bytes()
            import_report(case, root, "source-baseline")
            self.assertEqual(first, (root / "metric_observations.jsonl").read_bytes())
            rows = read_rows(root / "metric_observations.jsonl")
            self.assertTrue(any(row["available"] for row in rows))
            self.assertFalse(any(row["planning_eligible"] for row in rows))
            for row in rows:
                row.update(planning_eligible=True, attribution_status="verified", attribution_confidence="high", confidence="high", source_sha256="a" * 64)
            (root / "metric_observations.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            eligible, excluded = bound_evidence(root, "source-baseline", "a" * 64)
            self.assertEqual(eligible, [])
            self.assertTrue(any("metric_attribution_not_validated" in row["reasons"] for row in excluded))

    def test_binding_checks_keep_reports_distinct_and_reject_foreign_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {"evidence_id": "ev-one", "source_trial_id": "parent", "source_sha256": "a" * 64,
                   "metric": "register_count", "value": 64, "available": True, "source": "compiler_log",
                   "artifact_id": "TEST_DATA:report-one", "confidence": "medium", "parse_confidence": "high",
                   "attribution_status": "verified", "attribution_confidence": "medium", "scope": "kernel",
                   "kernel_name": "test_kernel", "device_id": "test_device", "shape_id": "shape-1", "dtype": "float16"}
            rows = [row, {**row, "evidence_id": "ev-two", "artifact_id": "TEST_DATA:report-two", "value": 96}]
            for key in ("device_id", "shape_id", "dtype", "source_sha256", "kernel_name"):
                rows.append({**row, "evidence_id": "foreign-" + key, key: "wrong"})
            (root / "metric_observations.jsonl").write_text("\n".join(json.dumps(item) for item in rows))
            binding = {"device_id": "test_device", "shape_id": "shape-1", "dtype": "float16", "kernel_names": ["test_kernel"]}
            eligible, excluded = bound_evidence(root, "parent", "a" * 64, binding)
            self.assertEqual([item["value"] for item in eligible], [64, 96])
            self.assertEqual(len(excluded), 5)

    def test_invalid_plan_retries_each_round_and_uses_recorded_rule_fallback(self):
        class BadReferences(FeedbackPlanner):
            def chat_json(self, messages):
                proposal = super().chat_json(messages)
                proposal["evidence_ids"] = ["fabricated-evidence"]
                return proposal

        _, result, planner, _, rounds = self.run_fixture(planner=BadReferences())
        self.assertEqual(len(planner.contexts), 4)
        self.assertEqual(len(result.trials), 2)
        for record in rounds[1:]:
            self.assertEqual(record["plan"]["source"], "rule_based")
            self.assertEqual(record["artifacts"]["planner_attempts"], 2)
            self.assertIn("invalid planner response", record["artifacts"]["fallback_reason"])
            self.assertEqual(record["evidence_ids"], [])

    def test_rejected_internal_reasoning_and_secrets_are_never_persisted(self):
        class BadExtra(FeedbackPlanner):
            def chat_json(self, messages):
                proposal = super().chat_json(messages)
                proposal["chain_of_thought"] = "FAKE_INTERNAL_REASONING_DO_NOT_SAVE"
                proposal["hypothesis"] = "FAKE_API_SECRET_FOR_ROUND_TEST"
                return proposal

        with patch.dict("os.environ", {"OPENAI_API_KEY": "FAKE_API_SECRET_FOR_ROUND_TEST"}):
            root, _, planner, _, _ = self.run_fixture(planner=BadExtra())
        for path in (root / "results").rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                self.assertNotIn(b"FAKE_INTERNAL_REASONING_DO_NOT_SAVE", content, path.name)
                self.assertNotIn(b"FAKE_API_SECRET_FOR_ROUND_TEST", content, path.name)

    def test_next_round_reads_new_parent_metrics_not_old_baseline_metrics(self):
        requests = []

        def collect(runner, results, request, **kwargs):
            requests.append(request)
            record = {"evidence_id": "TEST_DATA-" + request.source_trial_id, "source_trial_id": request.source_trial_id,
                      "source_sha256": request.source_sha256, "metric": "register_count", "value": 64 - len(requests),
                      "available": True, "source": "compiler_log", "artifact_id": "TEST_DATA:compiler-report-" + str(len(requests)),
                      "confidence": "medium", "parse_confidence": "high", "attribution_status": "verified",
                      "attribution_confidence": "medium", "scope": "kernel", "kernel_name": "fixture_kernel"}
            with (results / "metric_observations.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            payload = {"status": "collected", "source_trial_id": request.source_trial_id,
                       "source_sha256": request.source_sha256, "test_data": True,
                       "artifact_manifest": [{"path": "TEST_DATA:report-a", "sha256": "a" * 64},
                                             {"path": "TEST_DATA:report-b", "sha256": "b" * 64}]}
            return SimpleNamespace(to_dict=lambda redactor: redactor(payload))

        _, result, planner, calls, rounds = self.run_fixture(profiler=True, collector=collect)
        self.assertEqual(len(requests), 3)
        self.assertEqual(planner.contexts[0]["eligible_metrics"][0]["source_trial_id"], "source-baseline")
        self.assertEqual(planner.contexts[1]["eligible_metrics"][0]["source_trial_id"], result.trials[0].trial_id)
        self.assertNotEqual(planner.contexts[0]["authorized_evidence_ids"], planner.contexts[1]["authorized_evidence_ids"])
        all_ids = {item["evidence_id"] for context in planner.contexts for item in context["eligible_metrics"]}
        self.assertTrue(all(set(record["evidence_ids"]).issubset(all_ids) for record in rounds))
        for trial in result.trials:
            reports = trial.artifacts["profiler_collection"]["artifact_manifest"]
            self.assertEqual(len(reports), 2)
            self.assertNotEqual(reports[0]["sha256"], reports[1]["sha256"])
        for request in requests:
            source_calls = [name for path, name, _ in calls if path.parent.name == request.source_trial_id]
            if source_calls:
                self.assertLess(source_calls.index("correctness"), source_calls.index("benchmark_1"))

    def test_cancel_during_replanning_restores_baseline_and_starts_no_new_candidate(self):
        state = {"cancel": False}

        class CancelPlanner(FeedbackPlanner):
            def chat_json(self, messages):
                proposal = super().chat_json(messages)
                if len(self.contexts) == 2:
                    state["cancel"] = True
                return proposal

        root, result, _, calls, rounds = self.run_fixture(planner=CancelPlanner(), cancel=lambda: state["cancel"])
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(result.trials), 1)
        self.assertEqual(result.best_source_sha256, result.baseline_source_sha256)
        self.assertNotIn("TL.copy(", (root / "results/best_kernel.py").read_text())
        self.assertTrue(result.baseline["cancel_restore"]["verified"])
        self.assertEqual(rounds[-1]["plan"]["decision"], "stop")
        self.assertIn("cancelled during planning", rounds[-1]["acceptance"]["reason"])

    def test_hardware_claim_without_evidence_retries_and_falls_back(self):
        class UnsupportedClaim(FeedbackPlanner):
            def chat_json(self, messages):
                proposal = super().chat_json(messages)
                proposal.update(bottleneck_type="memory_bound", confidence="high", evidence_ids=[])
                return proposal

        _, result, planner, _, rounds = self.run_fixture(planner=UnsupportedClaim(), budget=1)
        self.assertEqual(len(planner.contexts), 2)
        self.assertEqual(rounds[1]["analysis"]["bottleneck_type"], "source_structure")
        self.assertEqual(rounds[1]["analysis"]["confidence"], "low")

    def test_profiler_unverified_stop_blocks_rewrite_and_later_rounds(self):
        def collect(*args, **kwargs):
            return SimpleNamespace(to_dict=lambda redactor: {
                "status": "failed", "reason_category": "cleanup_unverified", "message": "TEST_DATA: stop unverified",
            })

        with patch.object(engine, "_restore_execution_workspace") as restore:
            _, result, planner, _, rounds = self.run_fixture(profiler=True, collector=collect)
        restore.assert_not_called()
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.baseline_verified)
        self.assertEqual(planner.contexts, [])
        self.assertIn("unverified", result.reason)
        self.assertEqual(rounds[0]["acceptance"]["decision"], "failed")

    def test_cancel_before_candidate_runner_is_a_verified_local_restore(self):
        state = {"cancel": False}

        def event(kind, message):
            if kind == "source_trial_started":
                state["cancel"] = True

        _, result, _, calls, rounds = self.run_fixture(cancel=lambda: state["cancel"], on_event=event)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.trials[0].status, "cancelled")
        self.assertTrue(result.trials[0].rollback_verified)
        self.assertFalse(any(path.parent.name == result.trials[0].trial_id for path, _, _ in calls))
        self.assertEqual(rounds[-1]["acceptance"]["decision"], "cancelled")


if __name__ == "__main__":
    unittest.main()
