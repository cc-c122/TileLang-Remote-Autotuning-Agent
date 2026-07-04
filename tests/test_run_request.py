from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.run_request import RunRequest, app_config_from_run_request, load_config_from_run_request, load_run_request, write_effective_config_from_run_request


ROOT = Path(__file__).resolve().parents[1]
INLINE_KERNEL = """BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}
"""


def minimal_request(**overrides):
    data = {
        "schema_version": "v2.run_request.v1",
        "project_name": "my-task",
        "sample": {
            "source_type": "inline",
            "inline_text": INLINE_KERNEL,
            "path": None,
            "entry_file": "kernel.py",
        },
        "commands": {
            "build_command": "python -m py_compile kernel.py",
            "correctness_command": "python correctness.py",
            "benchmark_command": "python benchmark.py",
        },
        "target": {
            "gpu_model": "Metax C500",
            "backend": "unknown",
        },
        "settings_ref": {"use_saved_settings": True},
        "hardware_overrides": {"fields": {}},
        "search": {
            "strategy": "rule_based",
            "max_iterations": 1,
            "candidates_per_iteration": 3,
            "timeout_seconds": 60,
            "objective": "latency",
        },
        "profiler": {"enabled": True, "type": "dummy"},
        "patching": {"enabled": True},
    }
    for key, value in overrides.items():
        data[key] = value
    return data


class RunRequestTests(unittest.TestCase):
    def test_inline_run_request_generates_runnable_effective_config_without_base_config(self) -> None:
        request = RunRequest.model_validate(
            minimal_request(hardware_overrides={"fields": {"max_threads_per_block": 2048}})
        )
        with tempfile.TemporaryDirectory() as tmp:
            effective_path = Path(tmp) / "effective_config.yaml"
            config = write_effective_config_from_run_request(request, effective_path)
            effective = yaml.safe_load(effective_path.read_text(encoding="utf-8"))

        self.assertTrue(Path(config.kernel.sample_path).exists())
        self.assertEqual(config.kernel.entry_file, "kernel.py")
        self.assertEqual(config.kernel.run_command, "python benchmark.py")
        self.assertEqual(config.hardware.profile, "metax_c500")
        self.assertEqual(config.hardware.fields_source, "user_override")
        self.assertEqual(config.profiler.type, "dummy")
        self.assertTrue(config.patching.enabled)
        self.assertEqual(set(config.search_space), {"BM", "BN", "BK", "NUM_THREADS", "NUM_STAGES", "VECTOR_WIDTH"})
        self.assertEqual(effective["remote"]["password_env"], "KERNEL_AGENT_SSH_PASSWORD")
        self.assertEqual(effective["hardware"]["fields_source"], "user_override")
        self.assertEqual(effective["profiler"]["type"], "dummy")
        self.assertTrue(effective["patching"]["enabled"])
        self.assertNotIn("do-not-store", yaml.safe_dump(effective).lower())
        self.assertNotIn("api_key:", yaml.safe_dump(effective).lower())

    def test_path_run_request_reads_local_sample_path(self) -> None:
        request = RunRequest.model_validate(
            minimal_request(
                sample={
                    "source_type": "path",
                    "inline_text": None,
                    "path": str(ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal"),
                    "entry_file": "kernel.py",
                }
            )
        )
        config = app_config_from_run_request(request)
        self.assertEqual(Path(config.kernel.sample_path), ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal")
        self.assertIn("VECTOR_WIDTH", config.search_space)

    def test_upload_run_request_uses_materialized_path(self) -> None:
        request = RunRequest.model_validate(
            minimal_request(
                sample={
                    "source_type": "upload",
                    "inline_text": None,
                    "path": str(ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal"),
                    "entry_file": "kernel.py",
                }
            )
        )
        config = app_config_from_run_request(request)
        self.assertEqual(Path(config.kernel.sample_path), ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal")

    def test_frontend_exported_schema_file_runs_through_cli_config_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "run_request.yaml"
            request_path.write_text(yaml.safe_dump(minimal_request(), sort_keys=False), encoding="utf-8")
            config = load_config_from_run_request(request_path)
        self.assertEqual(config.project_name, "my-task")
        self.assertEqual(config.hardware.profile, "metax_c500")
        self.assertEqual(config.search.strategy, "rule_based")

    def test_run_request_user_hardware_overrides_win_over_profile_and_remote_detection(self) -> None:
        request = RunRequest.model_validate(
            minimal_request(
                hardware_overrides={
                    "fields": {
                        "total_memory_GB": 128,
                        "shared_memory_per_block_bytes": 65536,
                    }
                }
            )
        )
        config = app_config_from_run_request(request)
        config.hardware_detection.remote_detection = False
        config.hardware_detection.safe_probe = False
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_hardware(config, Path(tmp))

        self.assertEqual(info.profile_used, "metax_c500")
        self.assertEqual(info.fields["target_name"].value, "Metax C500")
        self.assertEqual(info.fields["target_name"].source, "user_override")
        self.assertEqual(info.fields["total_memory_GB"].value, 128)
        self.assertEqual(info.fields["total_memory_GB"].source, "user_override")
        self.assertEqual(info.fields["shared_memory_per_block_bytes"].value, 65536)
        self.assertEqual(info.fields["shared_memory_per_block_bytes"].source, "user_override")
        self.assertEqual(info.fields["vector_alignment_bytes"].value, 16)
        self.assertEqual(info.fields["vector_alignment_bytes"].source, "builtin_profile")

    def test_run_request_rejects_plaintext_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_request.yaml"
            data = minimal_request()
            data["password"] = "do-not-store"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sensitive value field is not allowed"):
                load_run_request(path)

    def test_entry_file_must_stay_inside_inline_workspace(self) -> None:
        data = minimal_request()
        data["sample"]["entry_file"] = "../kernel.py"
        with self.assertRaisesRegex(ValueError, "sample.entry_file"):
            RunRequest.model_validate(data)

    def test_v1_config_entry_remains_compatible(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        self.assertEqual(config.runner.type, "local")
        self.assertIn("BM", config.search_space)


if __name__ == "__main__":
    unittest.main()
