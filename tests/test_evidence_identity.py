"""Synthetic receipt tests only; no production calibration policy is enabled."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from kernel_opt_agent.profiler.attribution import annotate_planning_eligibility
from kernel_opt_agent.profiler.mcprofiler import attribution_validation as validation
from kernel_opt_agent.server.app import app
from kernel_opt_agent.server.models import TaskRecord
from kernel_opt_agent.server.task_manager import TaskManager
from kernel_opt_agent.source_optimizer.rounds import bound_evidence
from tests import test_mma_attribution_validation as fixtures


class EvidenceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MmaAttributionValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture._enable_test_policy()
        self.row, self.check = self.fixture._capture()
        self.calibration = self.fixture._calibration()
        self.row["source_trial_id"] = self.check.expected.source_trial_id
        self.root = self.fixture.root / "results"
        self.root.mkdir()
        manager = TaskManager(self.fixture.root / "tasks")
        record = TaskRecord(task_id="identity-test", status="completed", workspace=str(self.fixture.root), results_dir=str(self.root))
        manager.add_event(record.task_id, "task_completed", "TEST DATA", create_if_missing=record)
        manager_patch = patch("kernel_opt_agent.server.app.manager", manager)
        manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _sign(self, row):
        row = deepcopy(row)
        result = validation.validate_mma_collection(row, self.check, self.calibration)
        self.assertEqual(result["status"], "verified", result)
        row["attribution_validation"] = result
        return annotate_planning_eligibility(row)

    def _save_and_read(self, row, *, trial=None, source=None, binding=None):
        (self.root / "metric_observations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        response = self.client.get("/api/tasks/identity-test/results")
        self.assertEqual(response.status_code, 200)
        public = response.json()["results"]["evidence_summary"][0]
        eligible, excluded = bound_evidence(self.root, trial or self.check.expected.source_trial_id,
            source or self.check.expected.source_sha256, binding or {
                "device_id": self.check.expected.device_id, "shape_id": self.check.expected.shape_id,
                "dtype": self.check.expected.dtype, "kernel_names": [self.check.expected.kernel_name]})
        return public, eligible, excluded

    def test_issuance_rejects_reviewed_top_level_scope_conflicts(self):
        for field, wrong in (("source_sha256", "f" * 64), ("shape_id", "ANOTHER_SHAPE"), ("dtype", "float32")):
            with self.subTest(field=field):
                result = validation.validate_mma_collection({**self.row, field: wrong}, self.check, self.calibration)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(field + "_identity_conflict", result["reason_codes"])

    def test_reviewed_post_issuance_rebinding_is_rejected_by_api_and_planner(self):
        row = self._sign(self.row)
        for field, wrong in (("source_sha256", "f" * 64), ("shape_id", "ANOTHER_SHAPE"), ("dtype", "float32")):
            with self.subTest(field=field):
                tampered = {**row, field: wrong, "planning_eligible": True}
                result = validation.backend_validation(tampered)
                self.assertEqual(result["status"], "blocked")
                public, eligible, excluded = self._save_and_read(tampered,
                    source=wrong if field == "source_sha256" else None,
                    binding={"shape_id": wrong if field == "shape_id" else self.check.expected.shape_id,
                             "dtype": wrong if field == "dtype" else self.check.expected.dtype})
                self.assertFalse(public["planning_eligible"])
                self.assertEqual(eligible, [])
                self.assertEqual(len(excluded), 1)
                self.assertIn(field + "_identity_conflict", public["eligibility_reasons"])
                self.assertIn(field + "_identity_conflict", excluded[0]["reasons"])

    def test_every_collection_identity_field_rejects_conflicts_before_and_after_issuance(self):
        row = deepcopy(self.row)
        identity = asdict(self.check.expected)
        row.update(identity)
        row["collection_scope"].update(identity)
        signed = self._sign(row)
        for field in identity:
            for location in ("top", "scope"):
                with self.subTest(field=field, location=location):
                    conflict = deepcopy(row)
                    if location == "top":
                        conflict[field] = "WRONG_" + field
                    else:
                        conflict["collection_scope"][field] = "WRONG_" + field
                    issued = validation.validate_mma_collection(conflict, self.check, self.calibration)
                    self.assertEqual(issued["status"], "blocked")
                    self.assertIn(field + "_identity_conflict", issued["reason_codes"])
                    conflict["attribution_validation"] = signed["attribution_validation"]
                    self.assertEqual(validation.backend_validation(conflict)["status"], "blocked")
                    public, eligible, excluded = self._save_and_read(conflict)
                    self.assertFalse(public["planning_eligible"])
                    self.assertEqual(eligible, [])
                    self.assertIn(field + "_identity_conflict", excluded[0]["reasons"])

    def test_consistent_aliases_can_be_added_after_issuance_without_rebinding(self):
        row = deepcopy(self.row)
        identity = asdict(self.check.expected)
        row["collection_scope"].update(identity)
        signed = self._sign(row)
        for field, value in identity.items():
            with self.subTest(field=field):
                aliased = {**signed, field: value}
                self.assertEqual(validation.backend_validation(aliased)["status"], "verified")
                public, eligible, excluded = self._save_and_read(aliased)
                self.assertTrue(public["planning_eligible"], public["eligibility_reasons"])
                self.assertEqual(len(eligible), 1)
                self.assertEqual(excluded, [])

    def test_consistent_scope_aliases_and_observation_field_aliases_preserve_receipt(self):
        signed = self._sign(self.row)
        additions = {"device_id": self.check.expected.device_id, "kernel_name": self.check.expected.kernel_name,
                     "tool_version": self.check.expected.tool_version, "metric_name": signed["metric"],
                     "source_field_name": signed["source_field"], "artifact": signed["artifact_id"]}
        for field, value in additions.items():
            with self.subTest(field=field):
                aliased = deepcopy(signed)
                aliased["collection_scope"][field] = value
                self.assertEqual(validation.backend_validation(aliased)["status"], "verified")
                public, eligible, excluded = self._save_and_read(aliased)
                self.assertTrue(public["planning_eligible"])
                self.assertEqual(len(eligible), 1)
                self.assertEqual(excluded, [])

    def test_metric_source_field_and_artifact_alias_conflicts_are_rejected(self):
        signed = self._sign(self.row)
        for field, canonical in (("metric_name", "metric"), ("source_field_name", "source_field"), ("artifact", "artifact_id")):
            with self.subTest(field=field):
                conflict = {**signed, field: "WRONG"}
                result = validation.backend_validation(conflict)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(canonical + "_identity_conflict", result["reason_codes"])
                before_signing = {**self.row, field: "WRONG"}
                self.assertEqual(validation.validate_mma_collection(before_signing, self.check, self.calibration)["status"], "blocked")

    def test_changing_both_aliases_cannot_replace_backend_observed_identity(self):
        row = deepcopy(self.row)
        identity = asdict(self.check.expected)
        row.update(identity)
        row["collection_scope"].update(identity)
        signed = self._sign(row)
        for field in identity:
            with self.subTest(field=field):
                changed = deepcopy(signed)
                changed[field] = changed["collection_scope"][field] = "WRONG_" + field
                before_signing = validation.validate_mma_collection(changed, self.check, self.calibration)
                self.assertEqual(before_signing["status"], "blocked")
                self.assertIn("observation_" + field + "_mismatch", before_signing["reason_codes"])
                self.assertEqual(validation.backend_validation(changed)["status"], "blocked")
                self.assertEqual(self._save_and_read(changed)[1], [])

    def test_top_only_source_identity_uses_the_same_planner_semantics(self):
        row = deepcopy(self.row)
        for field in ("source_sha256", "shape_id", "dtype", "source_trial_id"):
            row[field] = row["collection_scope"].pop(field)
        signed = self._sign(row)
        public, eligible, excluded = self._save_and_read(signed)
        self.assertTrue(public["planning_eligible"])
        self.assertEqual(len(eligible), 1)
        self.assertEqual(excluded, [])

    def test_nonidentity_collection_context_remains_bound(self):
        signed = self._sign(self.row)
        for field, value in (("requested_kernel", "WRONG"), ("background_workload_excluded", False),
                             ("collection_window", {"target_only_window_verified": True})):
            with self.subTest(field=field):
                changed = deepcopy(signed)
                changed["collection_scope"][field] = value
                self.assertEqual(validation.backend_validation(changed)["status"], "blocked")
                self.assertEqual(self._save_and_read(changed)[1], [])

    def test_null_empty_or_malformed_alias_cannot_hide_a_conflict(self):
        signed = self._sign(self.row)
        for wrong in (None, "", [], {}, 0, False):
            with self.subTest(value=wrong):
                conflict = {**signed, "source_sha256": wrong}
                public, eligible, excluded = self._save_and_read(conflict)
                self.assertFalse(public["planning_eligible"])
                self.assertEqual(eligible, [])
                self.assertTrue(excluded[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
