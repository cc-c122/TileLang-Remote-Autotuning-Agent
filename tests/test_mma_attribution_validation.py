"""Deterministic TEST DATA, not C500 calibration or optimization evidence."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kernel_opt_agent.diagnosis.evidence import observation_to_evidence, planning_evidence_allowed
from kernel_opt_agent.profiler.attribution import annotate_planning_eligibility
from kernel_opt_agent.profiler.base import MetricObservation, ProfilerResult
from kernel_opt_agent.profiler.mcprofiler import attribution_validation as validation
from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.server.app import _result_payload


def digest(data):
    return hashlib.sha256(data).hexdigest()


class MmaAttributionValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.definition = validation.MmaDefinition(
            "TEST_DATA", "TEST_BUILD", "TEST_GPU", "TEST numerator", "TEST denominator", "percent", "kernel",
            "https://example.invalid/TEST_DATA_ONLY", "a" * 64, 0.1,
        )

    def _capture(self, name="target", value=20.0, kernel="split_kernel"):
        root = self.root / name
        root.mkdir()
        source = b"TEST_DATA_ONLY_SOURCE_NOT_A_GPU_WORKLOAD\n"
        ir = (b"define void @attribution_elementwise() {\nret void\n}\n"
              b"define void @attribution_mma() {\ncall void @llvm.mxc.mma.test()\nret void\n}\n")
        native = {"GPU Throughput Statistics": [{"name": "AP MMA Duty ratio", "data": value, "isError": False}]}
        config = {"conditions": {"kernelnames": [kernel], "include": True, "is_perkernel": True,
                                  "counts": 100, "profile_from_start": False}}
        files = {"source.py": source, "device.ll": ir, "report_dumped_result.json": json.dumps(native).encode(),
                 "mcProfiler.json": json.dumps(config).encode()}
        hashes = {key: digest(data) for key, data in files.items()}
        identity = validation.CollectionIdentity(name, "test-trial", hashes["source.py"], hashes["device.ll"],
            "device_sha256:" + "b" * 64, "TEST_B1_CTX128", "float16", kernel, "launch-4", "TEST_DATA", "TEST_BUILD", "TEST_GPU")
        proof = validation.NativeScopeProof(identity, hashes["report_dumped_result.json"], hashes["mcProfiler.json"], "kernel", True, True, 100)
        files["native_trace.json"] = json.dumps({"TEST_DATA_ONLY": True, **asdict(proof)}).encode()
        hashes["native_trace.json"] = digest(files["native_trace.json"])
        for filename, content in files.items():
            (root / filename).write_bytes(content)
        check = validation.CollectionCheck(root, identity, identity, hashes,
            report_path="report_dumped_result.json", config_path="mcProfiler.json", launch_trace_path="native_trace.json",
            source_path="source.py", generated_path="device.ll", counter_scope="kernel", window_verified=True,
            background_excluded=True, replay_count=100, observed_replay_count=100, configured_kernels=(kernel,))
        row = {"evidence_id": "ev_TEST_DATA", "metric": "mma_duty", "source_field": "AP MMA Duty ratio",
            "value": value, "unit": "percent", "source": "mcprofiler", "artifact_id": "TEST_DATA:" + hashes["report_dumped_result.json"],
            "available": True, "confidence": "high", "parse_confidence": "high", "kernel_name": kernel,
            "device_id": identity.device_id, "tool_version": identity.tool_version,
            "collection_scope": {"requested_kernel": kernel, "source_sha256": identity.source_sha256,
                "source_trial_id": identity.source_trial_id, "shape_id": identity.shape_id, "dtype": identity.dtype,
                "counter_scope": "kernel", "background_workload_excluded": True}}
        return row, check

    def _calibration(self):
        captures = []
        for mode in ("idle", "elementwise", "mma"):
            for repeat in range(2):
                row, check = self._capture(mode + str(repeat), value=20.0 if mode == "mma" else 0.0, kernel="attribution_" + mode)
                captures.append(validation.ControlCapture(mode, row["value"], check.expected.source_sha256,
                    check.expected.generated_sha256, 1 if mode == "mma" else 0, check.artifact_hashes[check.report_path],
                    True, True, check))
        return validation.MmaCalibration("TEST_DATA", "TEST_BUILD", "TEST_GPU", tuple(captures))

    @staticmethod
    def _test_reader(raw):
        # Installed only by tests, never in production or through an API/manifest.
        data = json.loads(raw)
        assert data.pop("TEST_DATA_ONLY") is True
        data["identity"] = validation.CollectionIdentity(**data["identity"])
        return validation.NativeScopeProof(**data)

    def _enable_test_policy(self):
        for name, values in (("_DOCUMENTED_MMA_DEFINITIONS", {("TEST_DATA", "TEST_BUILD", "TEST_GPU"): self.definition}),
                             ("_NATIVE_SCOPE_READERS", {("TEST_DATA", "TEST_BUILD"): self._test_reader})):
            mock = patch.object(validation, name, values)
            mock.start()
            self.addCleanup(mock.stop)

    def test_response_difference_and_claimed_flags_never_authorize_current_tool(self):
        row, check = self._capture()
        calibration = self._calibration()
        result = validation.validate_mma_collection(row, check, calibration)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("metric_definition_unconfirmed", result["reason_codes"])
        self.assertIn("native_scope_verifier_unavailable", result["reason_codes"])
        self.assertTrue(calibration.evaluate()["response_differentiation_observed"])
        self.assertIsNone(calibration.evaluate()["definition"])

    def test_only_backend_result_flows_through_observation_evidence_import_api(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        result = validation.validate_mma_collection(row, check, calibration)
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result, validation.validate_mma_collection(row, check, calibration))
        observation = MetricObservation("mma_duty", row["source_field"], row["value"], "percent", "mcprofiler", True, "high",
            row["artifact_id"], kernel_name=row["kernel_name"], device_id=row["device_id"], tool_version=row["tool_version"],
            collection_scope=row["collection_scope"], attribution_validation=result)
        evidence = observation_to_evidence(observation, "test-trial").to_dict()
        self.assertEqual(evidence["attribution_status"], "verified")
        self.assertTrue(evidence["planning_eligible"], evidence["eligibility_reasons"])
        output = self.root / "results"
        import_report(check.workspace_root, output, "test-trial", profiler_result=ProfilerResult(observations=[observation]))
        api = _result_payload(output)
        self.assertEqual(api["evidence_summary"][0]["attribution_validation"], result)
        self.assertTrue(api["evidence_summary"][0]["planning_eligible"])
        # The normal task path reparses the file after collection.
        parsed = MxmacaProfiler().collect({"mcprofiler_case_path": check.workspace_root,
            "mcprofiler_metric_validations": [observation.to_dict()]})
        # This synthetic report lacks a named-report label/collection sidecar,
        # so its different identity must not inherit a receipt.
        self.assertEqual(parsed.observations[0].attribution_status, "unverified")
        # Re-importing original artifacts does not recreate backend authority.
        import_report(check.workspace_root, output, "test-trial")
        self.assertFalse(_result_payload(output)["evidence_summary"][0]["planning_eligible"])

    def test_forged_receipt_manifest_request_or_llm_flags_are_rejected(self):
        row, _ = self._capture()
        for claim in ({}, {"status": "verified"}, {"validation_id": "mcv_forged", "status": "verified"}):
            forged = {**row, "scope": "kernel", "attribution_status": "verified", "attribution_confidence": "high",
                "metric_definition": {"status": "documented"}, "planning_eligible": True, "attribution_validation": claim}
            self.assertFalse(planning_evidence_allowed(forged))
            self.assertFalse(annotate_planning_eligibility(forged)["planning_eligible"])

    def test_identity_mismatch_source_device_shape_dtype_version_build_launch_and_kernel(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        for key in asdict(check.expected):
            with self.subTest(key=key):
                wrong = replace(check, expected=replace(check.expected, **{key: "wrong"}))
                result = validation.validate_mma_collection(row, wrong, calibration)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(key + "_mismatch", result["reason_codes"])

    def test_report_edit_after_validation_invalidates_receipt(self):
        self._enable_test_policy()
        row, check = self._capture()
        row["attribution_validation"] = validation.validate_mma_collection(row, check, self._calibration())
        (check.workspace_root / check.report_path).write_bytes(b"{}")
        computed = annotate_planning_eligibility(row)
        self.assertFalse(computed["planning_eligible"])
        self.assertIn("report_hash_mismatch", computed["eligibility_reasons"])

    def test_rehashing_changed_report_does_not_replace_native_trace_binding(self):
        self._enable_test_policy()
        row, check = self._capture()
        path = check.workspace_root / check.report_path
        native = json.loads(path.read_text())
        native["GPU Throughput Statistics"][0]["data"] = 99
        path.write_text(json.dumps(native))
        hashes = {**check.artifact_hashes, check.report_path: digest(path.read_bytes())}
        result = validation.validate_mma_collection({**row, "value": 99}, replace(check, artifact_hashes=hashes), self._calibration())
        self.assertIn("native_report_hash_mismatch", result["reason_codes"])
        self.assertEqual(result["status"], "blocked")

    def test_config_filter_window_or_replay_claim_is_not_effective_proof(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        for overrides, expected in (({"kernelnames": ["combine"]}, "native_kernel_filter_unconfirmed"),
                                    ({"profile_from_start": True}, "native_custom_window_unconfirmed"),
                                    ({"counts": 4}, "configured_replay_count_mismatch")):
            with self.subTest(overrides=overrides):
                path = check.workspace_root / check.config_path
                config = {"conditions": {"kernelnames": [row["kernel_name"]], "include": True, "is_perkernel": True,
                                         "profile_from_start": False, "counts": 100, **overrides}}
                path.write_text(json.dumps(config))
                changed = replace(check, artifact_hashes={**check.artifact_hashes, check.config_path: digest(path.read_bytes())})
                result = validation.validate_mma_collection(row, changed, calibration)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(expected, result["reason_codes"])

    def test_native_scope_background_and_repeated_launch_are_checked_independently(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        trace = check.workspace_root / check.launch_trace_path
        original = json.loads(trace.read_text(encoding="utf-8"))
        for key, value, expected in (("counter_scope", "device", "native_counter_scope_unconfirmed"),
                                     ("target_only_window", False, "native_collection_window_unverified"),
                                     ("background_excluded", False, "native_background_workload_not_excluded"),
                                     ("replay_count", 99, "native_replay_count_mismatch")):
            with self.subTest(key=key):
                trace.write_text(json.dumps({**original, key: value}), encoding="utf-8")
                changed = replace(check, artifact_hashes={**check.artifact_hashes, check.launch_trace_path: digest(trace.read_bytes())})
                result = validation.validate_mma_collection(row, changed, calibration)
                self.assertEqual(result["status"], "blocked")
                self.assertIn(expected, result["reason_codes"])

    def test_calibration_requires_artifacts_codegen_repeats_and_repeatability(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        missing = replace(calibration, captures=calibration.captures[:1])
        self.assertIn("calibration_repeats_missing", missing.evaluate()["reason_codes"])
        claims = replace(calibration, captures=tuple(replace(item, collection_check=None) for item in calibration.captures))
        self.assertIn("calibration_artifact_provenance_unverified", claims.evaluate()["reason_codes"])
        fake_codegen = replace(calibration, captures=tuple(replace(item, mma_instruction_count=5) for item in calibration.captures))
        self.assertIn("control_codegen_mismatch", fake_codegen.evaluate()["reason_codes"])
        unstable = replace(calibration, captures=(*calibration.captures[:-1], replace(calibration.captures[-1], value=200)))
        self.assertIn("calibration_repeatability_failed", unstable.evaluate()["reason_codes"])
        self.assertEqual(validation.validate_mma_collection(row, check, unstable)["status"], "blocked")

    def test_unavailable_unknown_metric_wrong_unit_and_workspace_escape_are_rejected(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        for field, value in (("available", False), ("value", None), ("value", float("nan")), ("unit", "cycles"), ("metric", "other")):
            with self.subTest(field=field):
                self.assertEqual(validation.validate_mma_collection({**row, field: value}, check, calibration)["status"], "blocked")
        escaped = replace(check, artifact_hashes={**check.artifact_hashes, "../outside": "a" * 64})
        self.assertIn("artifact_unavailable_or_unsafe", validation.validate_mma_collection(row, escaped, calibration)["reason_codes"])

    def test_receipt_cannot_be_rebound_to_other_trial_value_or_artifact(self):
        self._enable_test_policy()
        row, check = self._capture()
        row["attribution_validation"] = validation.validate_mma_collection(row, check, self._calibration())
        for key, value in (("value", 50), ("artifact_id", "other:sha"), ("device_id", "other"), ("kernel_name", "combine")):
            self.assertFalse(annotate_planning_eligibility({**row, key: value})["planning_eligible"])
        self.assertFalse(annotate_planning_eligibility({**row, "source_trial_id": "other"})["planning_eligible"])

    def test_receipt_is_not_authority_after_backend_restart(self):
        self._enable_test_policy()
        row, check = self._capture()
        row["attribution_validation"] = validation.validate_mma_collection(row, check, self._calibration())
        with patch.object(validation, "_RECEIPTS", {}):
            self.assertFalse(annotate_planning_eligibility(row)["planning_eligible"])

    def test_control_artifact_edit_or_retired_definition_invalidates_prior_receipt(self):
        self._enable_test_policy()
        row, check = self._capture()
        calibration = self._calibration()
        row["attribution_validation"] = validation.validate_mma_collection(row, check, calibration)
        with patch.object(validation, "_DOCUMENTED_MMA_DEFINITIONS", {}):
            self.assertFalse(annotate_planning_eligibility(row)["planning_eligible"])
        control = calibration.captures[-1].collection_check
        (control.workspace_root / control.report_path).write_text("{}", encoding="utf-8")
        self.assertFalse(annotate_planning_eligibility(row)["planning_eligible"])

    def test_serialized_outputs_do_not_echo_sensitive_validation_claims(self):
        secret = "SYNTHETIC_DO_NOT_LEAK_45921"
        observation = MetricObservation("mma_duty", "AP MMA Duty ratio", 20, "percent", "mcprofiler", True, "high", "test:artifact",
            attribution_validation={"status": "verified", "validation_id": secret,
                "secret": secret, "credential": secret, "authorization": secret, "bearer": secret, "private_key": secret})
        payload = ProfilerResult(observations=[observation]).to_dict()
        evidence = observation_to_evidence(observation).to_dict()
        self.assertNotIn(secret, json.dumps([payload, evidence]))
        self.assertFalse(evidence["planning_eligible"])
        _, check = self._capture()
        (check.workspace_root / "agent_collection_context.json").write_text(
            json.dumps({"tool_build": secret, "collection_id": secret}), encoding="utf-8")
        parsed = MxmacaProfiler().collect({"mcprofiler_case_path": check.workspace_root})
        self.assertNotIn(secret, json.dumps(parsed.to_dict()))

    def test_normal_live_task_persists_blocked_validator_reasons_through_api(self):
        from tests.test_mcprofiler_attribution import McProfilerAttributionTests

        record, _, db = McProfilerAttributionTests()._normal_trial(self.root)
        self.assertEqual(record["status"], "benchmark_ok")
        observation = next(row for row in record["metric_observations"] if row["metric"] == "mma_duty")
        collection = record["profiler"]["collection"]
        validation_result = collection["execution_manifest"]["metric_attribution_validation"][0]
        self.assertEqual(observation["attribution_validation"], validation_result)
        self.assertEqual(validation_result["status"], "blocked")
        self.assertIn("metric_definition_unconfirmed", validation_result["reason_codes"])
        api_row = next(row for row in _result_payload(db.results_dir)["evidence_summary"] if row["metric"] == "mma_duty")
        self.assertEqual(api_row["attribution_validation"], validation_result)
        self.assertFalse(api_row["planning_eligible"])

    def test_real_control_library_lookup_is_child_scoped_and_guarded(self):
        from scripts.calibrate_mcprofiler_mma import control_command
        from kernel_opt_agent.runner.command_guard import validate

        for mode in ("idle", "elementwise", "mma"):
            command = control_command(mode)
            self.assertTrue(command.startswith("env LD_LIBRARY_PATH="))
            self.assertNotIn("_MEI", command)
            self.assertTrue(validate(command, "/managed/calibration", "/managed/calibration").allowed)
        with self.assertRaises(ValueError):
            control_command("mma; shutdown")

    def test_control_label_does_not_overwrite_native_collection_mode(self):
        from scripts.calibrate_mcprofiler_mma import control_capture_record

        record = control_capture_record("mma", 2, {"mode": "remote_official_cli", "status": "collected"})
        self.assertEqual(record["mode"], "remote_official_cli")
        self.assertEqual(record["control_mode"], "mma")
        self.assertEqual(record["repeat"], 2)


if __name__ == "__main__":
    unittest.main()
