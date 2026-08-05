from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.profiler.mcprofiler.report_import import import_report
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.diagnosis import diagnose_from_evidence_records, profiler_observations_to_evidence
from kernel_opt_agent.profiler.base import MetricObservation, ProfilerResult
from kernel_opt_agent.runner.command_guard import validate
from kernel_opt_agent.storage.experiment_db import ExperimentDB


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcprofiler" / "real_v8_tc1_gate"
PAGED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcprofiler" / "synthetic_paged_attention_decode_c500_import"
WORKSPACE = Path(__file__).resolve().parents[1]


class McProfilerParserTests(unittest.TestCase):
    def test_fixture_manifest_sha256_matches_report(self) -> None:
        manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256((FIXTURE / "report_dumped_result.json").read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["sha256"])
        self.assertEqual(digest, "2eb3b6b3f3446fedf47d01d348df938c510a1ea8adfa58dd165040ec95de955f")

    def test_same_case_parses_deterministically(self) -> None:
        first = parse_mcprofiler_case(FIXTURE).to_dict()
        second = parse_mcprofiler_case(FIXTURE).to_dict()
        self.assertEqual(first, second)

    def test_synthetic_paged_attention_fixture_manifest_sha256_matches_report(self) -> None:
        manifest = json.loads((PAGED_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256((PAGED_FIXTURE / "report_dumped_result.json").read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["sha256"])
        self.assertEqual(digest, "bda53b6ed9eecc7eb884dac7fa5a08c589ce0611884afd73f74f05399f01dd7f")

    def test_synthetic_paged_attention_metadata_is_identified_without_gate_up_confusion(self) -> None:
        parsed = parse_mcprofiler_case(PAGED_FIXTURE)
        self.assertEqual(parsed.metadata["operator"], "Paged Attention Decode")
        self.assertEqual(parsed.metadata["target_subkernel"], "Paged Attention Decode")
        self.assertEqual(parsed.metadata["gpu_model"], "MetaX C500")
        self.assertEqual(parsed.metadata["shape"]["batch"], 1)
        self.assertEqual(parsed.metadata["shape"]["query_heads"], 32)
        self.assertEqual(parsed.metadata["shape"]["kv_heads"], 8)
        self.assertEqual(parsed.metadata["shape"]["head_dim"], 128)
        self.assertEqual(parsed.metadata["shape"]["page_size"], 16)
        self.assertEqual(parsed.metadata["shape"]["context_lengths"], [128, 512, 2048])
        self.assertNotEqual(parse_mcprofiler_case(FIXTURE).metadata.get("operator"), "Paged Attention Decode")
        public = json.dumps(parsed.to_dict(), ensure_ascii=False)
        self.assertIn("Paged Attention Decode Metadata", public)
        self.assertIn("block_table_layout", public)

    def test_synthetic_paged_attention_report_import_keeps_insufficient_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = import_report(PAGED_FIXTURE, Path(tmp))
            self.assertEqual(payload["metadata"]["operator"], "Paged Attention Decode")
            self.assertEqual(payload["metadata"]["collection_status"], "synthetic_parser_fixture_not_real_baseline")
            self.assertTrue((Path(tmp) / "parsed_mcprofiler_case.json").exists())
            self.assertTrue((Path(tmp) / "metric_observations.jsonl").exists())
            diagnoses = [json.loads(line) for line in (Path(tmp) / "diagnoses.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual({item["bottleneck_type"] for item in diagnoses}, {"insufficient_evidence"})

    def test_paged_attention_baseline_manifest_commands_pass_guard(self) -> None:
        manifest_path = WORKSPACE / "kernel_opt_agent" / "samples" / "paged_attention_decode" / "baseline_manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["operator"], "Paged Attention Decode")
        self.assertIn("blocked", manifest["status"])
        self.assertEqual(manifest["sample_source"]["upstream_commit"], "1d155f4b80865edfe0009ad952135b7afbd4f05a")
        self.assertEqual(len(manifest["sample_source"]["vendored_file_sha256"]), 64)
        self.assertEqual(manifest["sample_source"]["upstream_license_spdx"], "MIT")
        self.assertEqual(manifest["sshrunner_probe"]["connection"], "<redacted>")
        self.assertNotIn("host_redacted", manifest["sshrunner_probe"])
        self.assertNotIn("port", manifest["sshrunner_probe"])
        self.assertNotIn("username_redacted", manifest["sshrunner_probe"])
        self.assertNotIn("remote_workspace", manifest["sshrunner_probe"])
        for item in manifest["reproduction_commands"]["commands"]:
            result = validate(item["command"], WORKSPACE, WORKSPACE)
            self.assertTrue(result.allowed, f"{item['label']}: {result.reason}")
        notice = (manifest_path.parent / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("MIT License", notice)
        self.assertIn("Copyright (c) Tile-AI", notice)

    def test_paged_attention_runner_has_explicit_correctness_and_sample_protocol(self) -> None:
        runner_path = WORKSPACE / "kernel_opt_agent" / "samples" / "paged_attention_decode" / "run_paged_attention_decode.py"
        source = runner_path.read_text(encoding="utf-8")
        self.assertIn("torch.allclose(output, reference, atol=atol, rtol=rtol)", source)
        self.assertIn("max_error", source)
        self.assertIn("raise AssertionError", source)
        self.assertIn("one precompiled TileLang paged-attention kernel invocation per sample", source)
        self.assertIn("_compiled_kernel_call", source)
        self.assertNotIn("upstream.main(", source)
        self.assertNotIn("do_bench", source)
        self.assertNotIn("see upstream correctness output", source)

    def test_real_case_metrics_are_normalized_without_clamping_percentages(self) -> None:
        parsed = parse_mcprofiler_case(FIXTURE)
        by_metric = parsed.metric_map()
        self.assertEqual(parsed.metadata["case_name"], "v8_tc1_gate_fixed")
        self.assertEqual(parsed.metadata["exec_id"], "21d2af55-9005-46ac-a2ef-fa5a7c7663ff")
        self.assertEqual(parsed.metadata["target_subkernel"], "Gate/Up")
        self.assertEqual(parsed.metadata["shape"]["hidden"], 2048)
        self.assertEqual(by_metric["private_read_instructions"].value, 0)
        self.assertEqual(by_metric["private_write_instructions"].value, 0)
        self.assertAlmostEqual(by_metric["l2c_hit_rate"].value, 46.10, places=2)
        self.assertAlmostEqual(by_metric["dnoc_read_average_latency"].value, 283.70, places=2)
        self.assertAlmostEqual(by_metric["shared_memory_access_efficiency"].value, 57.98, places=2)
        self.assertEqual(by_metric["achieved_waves"].value, 1806372.0)
        self.assertEqual(by_metric["dispatched_waves"].value, 39968018.0)
        self.assertAlmostEqual(by_metric["mma_duty"].value, 43.66, places=2)
        self.assertEqual(by_metric["l2c_hit_rate"].unit, "percent")
        self.assertTrue(by_metric["l2c_hit_rate"].parse_warnings)
        self.assertTrue(parsed.unknown_rows)
        artifact_id = parsed.artifacts["artifact_id"]
        self.assertTrue(artifact_id.startswith("v8_tc1_gate_fixed:"))
        self.assertIn("2eb3b6b3f3446fed", artifact_id)
        self.assertEqual(by_metric["l2c_hit_rate"].artifact, artifact_id)

    def test_missing_report_file_degrades_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            shutil.copy2(FIXTURE / "manifest.json", case_dir / "manifest.json")
            parsed = parse_mcprofiler_case(case_dir)
            self.assertEqual(parsed.observations, [])
            self.assertIn("report_dumped_result.json not found", parsed.warnings)

    def test_corrupt_report_file_degrades_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            (case_dir / "report_dumped_result.json").write_text("{not valid json", encoding="utf-8")
            parsed = parse_mcprofiler_case(case_dir)
            self.assertEqual(parsed.observations, [])
            self.assertTrue(any("failed to parse report_dumped_result.json" in item for item in parsed.warnings))

    def test_mxmaca_profiler_reads_mcprofiler_case(self) -> None:
        result = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE)})
        self.assertAlmostEqual(result.l2c_hit_rate or 0, 46.10, places=2)
        self.assertAlmostEqual(result.dnoc_read_average_latency or 0, 283.70, places=2)
        self.assertAlmostEqual(result.shared_memory_access_efficiency or 0, 57.98, places=2)
        self.assertAlmostEqual(result.shared_conflict_cycles or 0, 3.36, places=2)
        self.assertIsNone(result.shared_bank_conflict)
        self.assertEqual(result.achieved_waves, 1806372.0)
        self.assertEqual(result.dispatched_waves, 39968018.0)
        self.assertTrue(result.available_metrics["l2c_hit_rate"])
        self.assertTrue(result.observations)
        self.assertIn("report_dumped_result", result.raw_artifact_refs)

    def test_sensitive_report_values_are_redacted_from_public_dict(self) -> None:
        secret_password = "hunter2-password-value"
        secret_token = "token-abc-123"
        secret_api_key = "api_key_live_123"
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            (case_dir / "manifest.json").write_text(
                json.dumps({"case_name": "secret-case", "api_key": secret_api_key}),
                encoding="utf-8",
            )
            (case_dir / "report_dumped_result.json").write_text(
                json.dumps(
                    {
                        "Memory Statistics": [
                            {"name": "Global Read Instructions", "data": secret_token, "isError": False, "message": ""},
                            {"name": "Unknown password field", "data": secret_password, "isError": False, "message": ""},
                        ],
                        "token_section": {"nested": secret_token},
                    }
                ),
                encoding="utf-8",
            )
            parsed = parse_mcprofiler_case(case_dir)
            public = json.dumps(parsed.to_dict(), ensure_ascii=False)
            self.assertIn("sensitive-looking field", "\n".join(parsed.warnings))
            self.assertNotIn(secret_password, public)
            self.assertNotIn(secret_token, public)
            self.assertNotIn(secret_api_key, public)
            self.assertIn("<redacted:secret>", public)

    def test_mxmaca_profiler_serialized_result_redacts_sensitive_observations(self) -> None:
        secret_value = "secret-observation-value"
        credential_value = "credential-report-value"
        authorization_value = "Authorization: Bearer token-abc-123"
        private_key_value = "private_key_pem_value"
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            (case_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "case_name": "secret-case",
                        "exec_id": "credential-exec",
                        "private_key": private_key_value,
                    }
                ),
                encoding="utf-8",
            )
            (case_dir / "report_dumped_result.json").write_text(
                json.dumps(
                    {
                        "Memory Statistics": [
                            {
                                "name": "Dnoc Read Average Latency",
                                "data": secret_value,
                                "isError": False,
                                "message": authorization_value,
                            },
                            {
                                "name": "Unknown credential field",
                                "data": credential_value,
                                "isError": False,
                                "message": "",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = MxmacaProfiler().collect({"mcprofiler_case_path": str(case_dir)})
            public = json.dumps(result.to_dict(), ensure_ascii=False)

            self.assertNotIn(secret_value, public)
            self.assertNotIn(credential_value, public)
            self.assertNotIn(authorization_value, public)
            self.assertNotIn(private_key_value, public)
            self.assertNotIn("Bearer token-abc-123", public)
            self.assertIn("<redacted:secret>", public)

    def test_evidence_and_diagnoses_are_deterministic_for_same_case(self) -> None:
        first_result = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE)})
        second_result = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE)})
        first_evidence = [item.to_dict() for item in profiler_observations_to_evidence(first_result, "trial-gate-up")]
        second_evidence = [item.to_dict() for item in profiler_observations_to_evidence(second_result, "trial-gate-up")]
        self.assertEqual(first_evidence, second_evidence)
        first_diagnoses = [
            item.to_dict() for item in diagnose_from_evidence_records(profiler_observations_to_evidence(first_result, "trial-gate-up"))
        ]
        second_diagnoses = [
            item.to_dict() for item in diagnose_from_evidence_records(profiler_observations_to_evidence(second_result, "trial-gate-up"))
        ]
        self.assertEqual(first_diagnoses, second_diagnoses)

    def test_diagnosis_evidence_ids_exist(self) -> None:
        result = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE)})
        evidence = profiler_observations_to_evidence(result, "trial-gate-up")
        evidence_ids = {item.evidence_id for item in evidence}
        diagnoses = diagnose_from_evidence_records(evidence)
        self.assertTrue(diagnoses)
        for diagnosis in diagnoses:
            for evidence_id in diagnosis.evidence_ids:
                self.assertIn(evidence_id, evidence_ids)
        self.assertEqual({item.bottleneck_type for item in diagnoses}, {"insufficient_evidence"})
        self.assertTrue(all("Paged Attention" not in json.dumps(item.to_dict()) for item in diagnoses))

    def test_supported_metrics_without_thresholds_do_not_force_bottleneck(self) -> None:
        result = MxmacaProfiler().collect({"mcprofiler_case_path": str(FIXTURE)})
        diagnoses = diagnose_from_evidence_records(profiler_observations_to_evidence(result, "trial-gate-up"))
        self.assertEqual(len(diagnoses), 1)
        self.assertEqual(diagnoses[0].bottleneck_type, "insufficient_evidence")
        self.assertTrue(diagnoses[0].evidence_ids)

    def test_private_memory_traffic_alone_is_insufficient_evidence(self) -> None:
        result = ProfilerResult(
            observations=[
                MetricObservation(
                    metric_name="private_read_instructions",
                    source_field_name="Private Read Instructions",
                    value=4,
                    unit="instructions",
                    source="mcprofiler",
                    available=True,
                    confidence="high",
                    artifact="case:sha",
                )
            ]
        )
        diagnoses = diagnose_from_evidence_records(profiler_observations_to_evidence(result, "trial-private"))
        self.assertEqual(diagnoses[0].bottleneck_type, "insufficient_evidence")

    def test_private_memory_traffic_with_compiler_evidence_can_support_spill_diagnosis(self) -> None:
        result = ProfilerResult(
            observations=[
                MetricObservation(
                    metric_name="private_read_instructions",
                    source_field_name="Private Read Instructions",
                    value=4,
                    unit="instructions",
                    source="mcprofiler",
                    available=True,
                    confidence="high",
                    artifact="case:sha",
                ),
                MetricObservation(
                    metric_name="private_memory_bytes",
                    source_field_name="compiler private_memory_bytes",
                    value=128,
                    unit="bytes",
                    source="tilelang_log",
                    available=True,
                    confidence="medium",
                    artifact="compile-log",
                ),
            ]
        )
        diagnoses = diagnose_from_evidence_records(profiler_observations_to_evidence(result, "trial-private"))
        self.assertEqual(diagnoses[0].bottleneck_type, "private_memory_spill")
        self.assertEqual(len(diagnoses[0].evidence_ids), 2)

    def test_unknown_metric_only_produces_insufficient_evidence(self) -> None:
        result = ProfilerResult(
            observations=[
                MetricObservation(
                    metric_name="unknown_backend_metric",
                    source_field_name="Unknown Field",
                    value=123,
                    unit="unknown",
                    source="mcprofiler",
                    available=True,
                    confidence="low",
                    artifact="case:sha",
                )
            ]
        )
        evidence = profiler_observations_to_evidence(result, "trial-unknown")
        diagnoses = diagnose_from_evidence_records(evidence)
        self.assertEqual([item.bottleneck_type for item in diagnoses], ["insufficient_evidence"])
        self.assertEqual(diagnoses[0].evidence_ids, [evidence[0].evidence_id])

    def test_no_profiler_degrades_to_insufficient_evidence(self) -> None:
        evidence = profiler_observations_to_evidence(ProfilerResult.empty(), "trial-empty")
        diagnoses = diagnose_from_evidence_records(evidence)
        self.assertEqual(evidence, [])
        self.assertEqual(diagnoses[0].bottleneck_type, "insufficient_evidence")
        self.assertEqual(diagnoses[0].confidence, "low")

    def test_evidence_outputs_are_written_and_redacted(self) -> None:
        secret_value = "credential-secret-token"
        with tempfile.TemporaryDirectory() as tmp:
            result = ProfilerResult(
                observations=[
                    MetricObservation(
                        metric_name="dnoc_read_average_latency",
                        source_field_name="Authorization Header",
                        value=secret_value,
                        unit="cycles",
                        source="mcprofiler",
                        available=True,
                        confidence="low",
                        artifact="case:sha",
                        parse_warnings=["Bearer token should disappear"],
                    )
                ]
            )
            evidence = [item.to_dict() for item in profiler_observations_to_evidence(result, "trial-secret")]
            diagnoses = [item.to_dict() for item in diagnose_from_evidence_records(profiler_observations_to_evidence(result, "trial-secret"))]
            db = ExperimentDB(Path(tmp))
            db.append(
                {
                    "run_id": "run",
                    "iteration": 0,
                    "candidate_id": 0,
                    "trial_id": "trial-secret",
                    "config_hash": "hash",
                    "status": "benchmark_ok",
                    "profiler": {"enabled": True, "type": "mxmaca", "result": result.to_dict(), "error": None},
                    "metric_observations": evidence,
                    "diagnoses": diagnoses,
                    "bottleneck_diagnosis": [],
                    "metrics": {},
                    "objective": {},
                    "paths": {},
                }
            )
            for name in ("experiments.jsonl", "profiler_results.jsonl", "metric_observations.jsonl", "diagnoses.jsonl"):
                text = (Path(tmp) / name).read_text(encoding="utf-8")
                self.assertTrue(text.strip())
                self.assertNotIn(secret_value, text)
                self.assertNotIn("Bearer token should disappear", text)
            self.assertIn("<redacted:secret>", (Path(tmp) / "metric_observations.jsonl").read_text(encoding="utf-8"))

    def test_fixture_does_not_contain_plaintext_secrets(self) -> None:
        forbidden = ("password", "api_key", "token", "Cc.051026")
        for fixture_root in (FIXTURE, PAGED_FIXTURE):
            for path in fixture_root.rglob("*"):
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="ignore").lower()
                    for marker in forbidden:
                        self.assertNotIn(marker.lower(), text, str(path))


if __name__ == "__main__":
    unittest.main()
