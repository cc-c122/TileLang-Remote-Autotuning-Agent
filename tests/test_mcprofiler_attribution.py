from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kernel_opt_agent.config_model import AppConfig
from kernel_opt_agent.diagnosis import EvidenceBundle, diagnose_bottlenecks, diagnose_from_evidence_records, profiler_observations_to_evidence
from kernel_opt_agent.diagnosis.evidence import planning_evidence_allowed
from kernel_opt_agent.main import run_trial
from kernel_opt_agent.profiler.base import MetricObservation, ProfilerResult, UNVERIFIED_ATTRIBUTION_WARNING
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.profiler.registry import collect_mcprofiler_case
from kernel_opt_agent.source_optimizer.analyzer import analyze_source
from kernel_opt_agent.source_optimizer.engine import _collect_source_profiler, run_source_optimization
from kernel_opt_agent.source_optimizer.planner import choose_plan
from kernel_opt_agent.storage.experiment_db import ExperimentDB
from tests.test_mcprofiler_collection import FakeRemoteCollectionRunner
from tests.test_mcprofiler_real_c500_collection import FIXTURE, PRIMARY
from tests.test_source_optimizer import FakeRunner, RecordingPlanner, _config, _source


def _verified_row(evidence_id="verified", **overrides):
    row = {
        "evidence_id": evidence_id, "metric": "register_count", "value": 42,
        "available": True, "confidence": "high", "parse_confidence": "high",
        "attribution_status": "verified", "attribution_confidence": "high",
        "scope": "kernel", "kernel_name": "fixture_kernel", "source_trial_id": "source-baseline",
        "source": "compiler_log", "artifact_id": "fixture:compiler-log",
    }
    row.update(overrides)
    return row


class McProfilerAttributionTests(unittest.TestCase):
    def _normal_trial(self, root, runner_type=FakeRemoteCollectionRunner, kernel_names=()):
        trial = root / "trial"
        trial.mkdir()
        entry = trial / "kernel.py"
        entry.write_text("print('fixture')\n", encoding="utf-8")
        cfg = AppConfig.model_validate({
            "project_name": "normal-profiler-regression", "execution_mode": "baseline_only",
            "runner": {"type": "ssh"},
            "remote": {"host": "example.invalid", "username": "fixture", "auth_type": "key",
                       "key_path": "unused", "remote_workspace": "/managed/task"},
            "kernel": {"sample_path": str(trial), "entry_file": "kernel.py", "build_command": "build",
                       "correctness_command": "correctness", "run_command": "benchmark"},
            "search_space": {}, "hardware_detection": {"enabled": False},
            "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": True, "kernel_names": list(kernel_names)},
        })
        runner = runner_type(root / "remote", fixture=FIXTURE)
        db = ExperimentDB(root / "results")
        paths = {"trial_dir": trial, "kernel": entry, "kernel_copy": entry, "patch": trial / "patch.diff"}
        with patch("kernel_opt_agent.main.build_runner", return_value=runner):
            record = run_trial(cfg, db, "run", 0, 0, {}, paths)
        return record, runner, db

    def test_normal_task_selects_kernel_not_aggregate_without_manual_per_kernel_switch(self):
        for names in ((), ("profiler_vector_axpby",)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                record, runner, db = self._normal_trial(Path(tmp), kernel_names=names)
                self.assertEqual(record["status"], "benchmark_ok")
                collection = record["profiler"]["collection"]
                self.assertEqual(collection["status"], "collected")
                self.assertEqual(collection["primary_report"], PRIMARY)
                self.assertEqual(collection["execution_manifest"]["report_scope"], "named_kernel")
                metrics = {row["metric"]: row for row in record["metric_observations"]}
                self.assertEqual(metrics["global_read_instructions"]["value"], 851256)
                self.assertNotEqual(metrics["global_read_instructions"]["value"], 1030268527)
                self.assertIn(UNVERIFIED_ATTRIBUTION_WARNING, collection["warnings"])
                self.assertTrue(all(not planning_evidence_allowed(row) for row in metrics.values()))
                self.assertTrue(all(row["confidence"] == "low" and row["parse_confidence"] == "high" for row in metrics.values()))
                self.assertTrue(all(row["attribution_status"] == "unverified" for row in metrics.values()))
                command = next(command for name, command in runner.commands if name == "mcprofiler_collection")
                self.assertIn("--per-kernel", command)
                saved = [json.loads(line) for line in (db.results_dir / "metric_observations.jsonl").read_text().splitlines()]
                self.assertEqual({row["metric"]: row["value"] for row in saved}["global_read_instructions"], 851256)

    def test_normal_task_does_not_fall_back_to_aggregate_when_target_is_missing_or_ambiguous(self):
        for mode in ("missing", "ambiguous"):
            class IncompleteCaseRunner(FakeRemoteCollectionRunner):
                def run_cancellable(self, *args, **kwargs):
                    result = super().run_cancellable(*args, **kwargs)
                    primary = next(self.remote_root.rglob(PRIMARY))
                    if mode == "missing":
                        primary.unlink()
                    else:
                        shutil.copy2(primary, primary.parent / "3_other_kernel_dumped_result.json")
                    return result

            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                record, _, _ = self._normal_trial(Path(tmp), IncompleteCaseRunner)
                self.assertEqual(record["status"], "benchmark_ok")
                self.assertEqual(record["profiler"]["collection"]["reason_category"], "target_report_not_found")
                self.assertEqual(record["metric_observations"], [])
                self.assertEqual(record["profiler"]["result"]["latency_ms"], 1.25)

    def test_source_task_collector_also_selects_unique_kernel_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "sample"
            sample.mkdir()
            (sample / "kernel.py").write_text(_source(), encoding="utf-8")
            cfg = _config(sample)
            cfg.profiler.enabled, cfg.profiler.type, cfg.profiler.auto_collect = True, "mxmaca", True
            runner = FakeRemoteCollectionRunner(root / "remote", fixture=FIXTURE)
            result = _collect_source_profiler(cfg, root / "results", SimpleNamespace(status="benchmark_ok", runner=runner),
                                              "source-baseline", "a" * 64, lambda: False, None)
            self.assertEqual(result["status"], "collected")
            self.assertEqual(result["primary_report"], PRIMARY)
            self.assertEqual(next(row["value"] for row in result["metric_observations"] if row["metric_name"] == "global_read_instructions"), 851256)

    def test_import_cannot_promote_attribution_from_user_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "case"
            shutil.copytree(FIXTURE, copy)
            manifest = json.loads((copy / "manifest.json").read_text())
            manifest.update({"attribution_status": "verified", "attribution_confidence": "high", "confidence": "high"})
            (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            parsed = parse_mcprofiler_case(copy / PRIMARY)
            self.assertIn(UNVERIFIED_ATTRIBUTION_WARNING, parsed.warnings)
            self.assertTrue(all(row.attribution_status == "unverified" for row in parsed.observations))
            self.assertTrue(all(row.confidence == "low" and row.parse_confidence == "high" for row in parsed.observations))

    def test_planner_rejects_unverified_old_and_bare_evidence_ids_even_if_confidence_high(self):
        analysis = analyze_source(_source())
        row = _verified_row("untrusted", value=851256, attribution_status="unverified", confidence="high")
        legacy = {"evidence_id": "legacy", "available": True, "value": 1030268527, "confidence": "high"}
        client = RecordingPlanner({"target_id": analysis.targets[0].target_id, "hypothesis": "fixture",
                                   "template": analysis.targets[0].template, "evidence_ids": ["untrusted"]})
        plan, source = choose_plan(analysis.targets, [row, legacy, "bare-id"], client)
        self.assertEqual(source, "rule_based")
        self.assertEqual(len(client.messages), 2)
        self.assertEqual(plan.evidence_ids, [])
        for messages in client.messages:
            prompt = messages[0]["content"]
            for excluded in ("untrusted", "legacy", "bare-id", "851256", "1030268527"):
                self.assertNotIn(excluded, prompt)
            self.assertIn("target-attribution-verified", prompt)

    def test_attribution_gate_fails_closed_for_partial_or_malformed_metadata(self):
        self.assertTrue(planning_evidence_allowed(_verified_row()))
        for key, value in (("scope", "aggregate"), ("attribution_status", "unknown"),
                           ("attribution_confidence", "low"), ("attribution_confidence", []),
                           ("kernel_name", None), ("parse_confidence", "low"), ("value", float("nan")),
                           ("value", 10 ** 400), ("available", False), ("source", "unknown"),
                           ("metric", "l2c_hit_rate"), ("metric", [])):
            with self.subTest(key=key, value=str(value)[:20]):
                self.assertFalse(planning_evidence_allowed(_verified_row(**{key: value})))

    def test_unverified_private_traffic_cannot_support_spill_even_with_compiler_evidence(self):
        profiler = ProfilerResult(observations=[
            MetricObservation("private_read_instructions", "Private Read Instructions", 4, "instructions", "mcprofiler", True, "high", "case:sha"),
            MetricObservation("private_memory_bytes", "compiler private bytes", 128, "bytes", "tilelang_log", True, "medium", "log:sha"),
        ])
        diagnoses = diagnose_from_evidence_records(profiler_observations_to_evidence(profiler, "baseline"))
        self.assertEqual({row.bottleneck_type for row in diagnoses}, {"insufficient_evidence"})
        legacy = diagnose_bottlenecks(EvidenceBundle(profiler))
        self.assertEqual({row.bottleneck_type for row in legacy}, {"insufficient_evidence"})

    def test_real_imported_baseline_evidence_is_excluded_before_source_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "sample"
            sample.mkdir()
            entry = sample / "kernel.py"
            entry.write_text(_source(), encoding="utf-8")
            (sample / "correctness.py").write_text("protected", encoding="utf-8")
            cfg = _config(sample)
            cfg.profiler.enabled, cfg.profiler.type, cfg.profiler.auto_collect = True, "mxmaca", True
            results = root / "results"
            target = analyze_source(_source()).targets[0]

            class UntrustedCitationPlanner(RecordingPlanner):
                def chat_json(self, messages):
                    evidence = json.loads((results / "metric_observations.jsonl").read_text().splitlines()[0])
                    self.messages.append(messages)
                    return {"target_id": target.target_id, "template": target.template, "hypothesis": "fixture",
                            "evidence_ids": [evidence["evidence_id"]]}

            planner = UntrustedCitationPlanner({})
            calls = []

            def collect(runner, results_dir, request, **kwargs):
                self.assertTrue(request.per_kernel)
                calls.append(request)
                remote = FakeRemoteCollectionRunner(root / ("remote-" + str(len(calls))), fixture=FIXTURE)
                result = collect_mcprofiler_case(remote, results_dir, request, **kwargs)
                if request.source_trial_id == "source-baseline":
                    with (results_dir / "metric_observations.jsonl").open("a") as handle:
                        handle.write(json.dumps(_verified_row("foreign", source_trial_id="other-trial")) + "\n")
                return result

            with patch("kernel_opt_agent.source_optimizer.engine.build_runner", side_effect=lambda config, trial_dir: FakeRunner(trial_dir)), patch(
                "kernel_opt_agent.source_optimizer.engine.collect_remote_mcprofiler_case", side_effect=collect,
            ):
                result = run_source_optimization(cfg, root / "workspace", results, analyze_source(_source()), entry, planning_client=planner)
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(calls), 2)
            trial = result.trials[0]
            self.assertEqual(trial.evidence_ids, [])
            self.assertEqual(trial.artifacts["planner"]["source"], "rule_based")
            self.assertEqual(trial.artifacts["planner"]["excluded_evidence_count"], 13)
            self.assertTrue(trial.artifacts["planner"]["evidence_warnings"])
            for messages in planner.messages:
                self.assertNotIn("851256", messages[0]["content"])
                self.assertNotIn("foreign", messages[0]["content"])
                self.assertNotIn("global_read_instructions", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
