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
            minimal_request(
                settings_ref={"use_saved_settings": False},
                hardware_overrides={"fields": {"max_threads_per_block": 2048}},
            )
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
                },
                settings_ref={"use_saved_settings": False},
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
                },
                settings_ref={"use_saved_settings": False},
            )
        )
        config = app_config_from_run_request(request)
        self.assertEqual(Path(config.kernel.sample_path), ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal")

    def test_frontend_exported_schema_file_runs_through_cli_config_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "run_request.yaml"
            request_path.write_text(yaml.safe_dump(minimal_request(settings_ref={"use_saved_settings": False}), sort_keys=False), encoding="utf-8")
            config = load_config_from_run_request(request_path)
        self.assertEqual(config.project_name, "my-task")
        self.assertEqual(config.hardware.profile, "metax_c500")
        self.assertEqual(config.search.strategy, "rule_based")

    def test_frontend_shape_settings_yaml_starts_ssh_runner(self) -> None:
        settings = {
            "runner": {"type": "ssh"},
            "remote": {
                "host": "ssh.frontend.internal",
                "port": 2222,
                "username": "root",
                "auth_type": "password",
                "password_env": "KERNEL_AGENT_SSH_PASSWORD",
                "remote_workspace": "/tmp/kernel-agent",
            },
            "llm": {
                "provider": "openai_compatible",
                "base_url": "https://llm.example/v1",
                "model": "model-from-settings",
                "api_key_env": "OPENAI_API_KEY",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            settings_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            config = app_config_from_run_request(RunRequest.model_validate(minimal_request()), settings_path=settings_path)
        self.assertEqual(config.runner.type, "ssh")
        self.assertEqual(config.remote.host, "ssh.frontend.internal")
        self.assertEqual(config.remote.password_env, "KERNEL_AGENT_SSH_PASSWORD")
        self.assertEqual(config.llm.model, "model-from-settings")
        self.assertEqual(config.llm.api_key_env, "OPENAI_API_KEY")

    def test_saved_settings_merge_into_effective_config_without_secret_values(self) -> None:
        settings = {
            "runner": {"type": "ssh"},
            "remote": {
                "host": "ssh.example.internal",
                "port": 32222,
                "username": "root+vm-test",
                "auth_type": "password",
                "password_env": "KERNEL_AGENT_SSH_PASSWORD",
                "remote_workspace": "/data/kernel workspace",
            },
            "llm": {
                "base_url": "https://llm.example/v1",
                "model": "test-model",
                "api_key_env": "OPENAI_API_KEY",
            },
        }
        request = RunRequest.model_validate(minimal_request())
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            effective_path = Path(tmp) / "effective_config.yaml"
            settings_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            config = write_effective_config_from_run_request(request, effective_path, settings_path=settings_path)
            effective_text = effective_path.read_text(encoding="utf-8")
            effective = yaml.safe_load(effective_text)

        self.assertEqual(config.runner.type, "ssh")
        self.assertEqual(effective["remote"]["host"], "ssh.example.internal")
        self.assertEqual(effective["remote"]["password_env"], "KERNEL_AGENT_SSH_PASSWORD")
        self.assertEqual(effective["llm"]["api_key_env"], "OPENAI_API_KEY")
        self.assertNotIn("real-password", effective_text)
        self.assertNotIn("sk-real-secret", effective_text)
        self.assertNotIn("password:", effective_text.lower())

    def test_missing_saved_settings_fails_clearly(self) -> None:
        request = RunRequest.model_validate(minimal_request())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "user settings file not found"):
                app_config_from_run_request(request, settings_path=Path(tmp) / "missing.yaml")

    def test_use_saved_settings_false_allows_local_mock(self) -> None:
        request = RunRequest.model_validate(minimal_request(settings_ref={"use_saved_settings": False}))
        config = app_config_from_run_request(request)
        self.assertEqual(config.runner.type, "local")

    def test_saved_settings_cannot_enable_local_mock(self) -> None:
        settings = {"runner": {"type": "local"}}
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            settings_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "use_saved_settings=false for local mock"):
                app_config_from_run_request(RunRequest.model_validate(minimal_request()), settings_path=settings_path)

    def test_plaintext_secret_in_settings_is_rejected(self) -> None:
        settings = {
            "runner": {"type": "ssh"},
            "remote": {
                "host": "ssh.example.internal",
                "port": 22,
                "username": "root",
                "auth_type": "password",
                "password": "real-password",
                "password_env": "KERNEL_AGENT_SSH_PASSWORD",
                "remote_workspace": "/tmp/kernel-agent",
            },
            "llm": {"api_key": "sk-real-secret", "api_key_env": "OPENAI_API_KEY"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            settings_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sensitive value field is not allowed in user settings"):
                app_config_from_run_request(RunRequest.model_validate(minimal_request()), settings_path=settings_path)

    def test_run_request_user_hardware_overrides_win_over_profile_and_remote_detection(self) -> None:
        request = RunRequest.model_validate(
            minimal_request(
                settings_ref={"use_saved_settings": False},
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
