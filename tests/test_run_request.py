from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.run_request import RunRequest, app_config_from_run_request, write_effective_config_from_run_request


ROOT = Path(__file__).resolve().parents[1]


class RunRequestTests(unittest.TestCase):
    def test_run_request_generates_runnable_effective_config_without_base_config(self) -> None:
        request = RunRequest(
            sample_path="./samples/tilelang_mock_minimal",
            entry_file="kernel.py",
            gpu_model="Metax C500",
            build_command="python3 -m py_compile kernel.py",
            correctness_command="python3 correctness.py",
            benchmark_command="python3 benchmark.py",
            hardware_overrides={"max_threads_per_block": 2048},
        )
        with tempfile.TemporaryDirectory() as tmp:
            effective_path = Path(tmp) / "effective_config.yaml"
            config = write_effective_config_from_run_request(request, effective_path)
            effective = yaml.safe_load(effective_path.read_text(encoding="utf-8"))

        self.assertEqual(config.kernel.sample_path, "./samples/tilelang_mock_minimal")
        self.assertEqual(config.kernel.run_command, "python3 benchmark.py")
        self.assertEqual(config.hardware.profile, "metax_c500")
        self.assertEqual(config.hardware.fields_source, "user_override")
        self.assertEqual(set(config.search_space), {"BM", "BN", "BK", "NUM_THREADS", "NUM_STAGES", "VECTOR_WIDTH"})
        self.assertEqual(effective["remote"]["password_env"], "KERNEL_AGENT_SSH_PASSWORD")
        self.assertNotIn("do-not-store", yaml.safe_dump(effective).lower())
        self.assertNotIn("api_key:", yaml.safe_dump(effective).lower())

    def test_run_request_user_hardware_overrides_win_over_profile_and_remote_detection(self) -> None:
        request = RunRequest(
            sample_path="./samples/tilelang_mock_minimal",
            gpu_model="Metax C500",
            correctness_command="python3 correctness.py",
            benchmark_command="python3 benchmark.py",
            hardware_overrides={
                "total_memory_GB": 128,
                "shared_memory_per_block_bytes": 65536,
            },
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
            path.write_text(
                """
sample_path: ./samples/tilelang_mock_minimal
correctness_command: python3 correctness.py
benchmark_command: python3 benchmark.py
password: do-not-store
""",
                encoding="utf-8",
            )
            from kernel_opt_agent.run_request import load_run_request

            with self.assertRaisesRegex(ValueError, "sensitive value field is not allowed"):
                load_run_request(path)

    def test_v1_config_entry_remains_compatible(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        self.assertEqual(config.runner.type, "local")
        self.assertIn("BM", config.search_space)


if __name__ == "__main__":
    unittest.main()
