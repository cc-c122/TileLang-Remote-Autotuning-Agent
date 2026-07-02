from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.hardware.safe_probe import run_safe_probes
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.hardware.profile_loader import HardwareProfileLoader


ROOT = Path(__file__).resolve().parents[1]


class FakeDetectionRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands: list[str] = []

    def run(self, name, command):
        self.commands.append(name)
        stdout, stderr, returncode = self.responses.get(name, ("{}", "", 0))
        now = time.time()
        return CommandResult(name, command or "", returncode, stdout, stderr, now, now, 0.0)


class FakeProbeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands: list[str] = []

    def run(self, name, command):
        self.commands.append(name)
        response = self.responses.get(name)
        if isinstance(response, Exception):
            raise response
        stdout, stderr, returncode, timeout = response or ("{}", "", 0, False)
        now = time.time()
        return CommandResult(name, command or "", returncode, stdout, stderr, now, now, 0.0, timeout=timeout)


class HardwareDetectionTests(unittest.TestCase):
    def test_profile_loader_loads_builtin_profiles(self) -> None:
        loader = HardwareProfileLoader()
        unknown_name, unknown = loader.load("unknown_gpu")
        metax_name, metax = loader.load("metax_c500")
        self.assertEqual(unknown_name, "unknown_gpu")
        self.assertEqual(unknown["source"], "builtin_profile")
        self.assertEqual(metax_name, "metax_c500")
        self.assertEqual(metax["total_memory_GB"], 64)

    def test_user_config_overrides_profile(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware.profile = "metax_c500"
        config.hardware.target_name = "custom-metax"
        config.hardware.fields = {
            "total_memory_GB": 96,
            "shared_memory_per_block_bytes": 65536,
        }
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_hardware(config, Path(tmp))
        self.assertEqual(info.fields["target_name"].value, "custom-metax")
        self.assertEqual(info.fields["target_name"].source, "user_config")
        self.assertEqual(info.fields["total_memory_GB"].value, 96)
        self.assertEqual(info.fields["total_memory_GB"].source, "user_config")
        self.assertEqual(info.fields["shared_memory_per_block_bytes"].value, 65536)
        self.assertEqual(info.fields["shared_memory_per_block_bytes"].source, "user_config")
        self.assertEqual(info.fields["vector_alignment_bytes"].value, 16)
        self.assertEqual(info.fields["vector_alignment_bytes"].source, "builtin_profile")

    def test_unknown_fields_remain_null(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware.profile = "metax_c500"
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_hardware(config, Path(tmp))
        self.assertIsNone(info.fields["shared_memory_per_block_bytes"].value)
        self.assertEqual(info.fields["shared_memory_per_block_bytes"].source, "unknown")
        self.assertIn("shared_memory_per_block_bytes", info.unknown_fields())

    def test_detection_failure_still_writes_outputs(self) -> None:
        class FailingLoader:
            def load(self, profile_name):
                raise RuntimeError("profile boom")

        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware.profile = "metax_c500"
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            info = detect_hardware(config, results, profile_loader=FailingLoader())
            detected = yaml.safe_load((results / "hardware_detected.yaml").read_text(encoding="utf-8"))
            log_text = (results / "hardware_detection.log").read_text(encoding="utf-8")
            self.assertTrue((results / "hardware_probe.jsonl").exists())
        self.assertIn("hardware detection failed", info.warnings[0])
        self.assertIn("hardware detection failed", log_text)
        self.assertIn("fields", detected)
        self.assertIsNone(detected["fields"]["total_memory_GB"]["value"])

    def test_remote_detection_partial_success_logs_failures(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware_detection.builtin_profile = False
        runner = FakeDetectionRunner(
            {
                "hardware_python_platform": ('{"python_version": "3.11.9", "platform": "Linux-test", "os": "linux"}\n', "", 0),
                "hardware_tilelang_version": ("", "no tilelang", 1),
                "hardware_mctilelang_version": ('{"mctilelang_version": "0.1.0"}\n', "", 0),
                "hardware_compiler_version": ('{"compiler_version": "gcc 12.2.0"}\n', "", 0),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            info = detect_hardware(config, results, command_runner=runner)
            log_text = (results / "hardware_detection.log").read_text(encoding="utf-8")
        self.assertEqual(info.fields["python_version"].value, "3.11.9")
        self.assertEqual(info.fields["python_version"].source, "remote_detection")
        self.assertEqual(info.fields["compiler_version"].value, "gcc 12.2.0")
        self.assertEqual(info.fields["mctilelang_version"].value, "0.1.0")
        self.assertIn("remote detection command failed: tilelang_version", log_text)

    def test_remote_detection_failure_does_not_interrupt(self) -> None:
        class ExplodingRunner:
            def run(self, name, command):
                raise RuntimeError("runner boom")

        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            info = detect_hardware(config, results, command_runner=ExplodingRunner())
            log_text = (results / "hardware_detection.log").read_text(encoding="utf-8")
            self.assertTrue((results / "hardware_detected.yaml").exists())
        self.assertIn("remote detection command failed: python_platform: runner boom", log_text)
        self.assertIn("hardware fields remain unknown", "\n".join(info.warnings))

    def test_user_config_overrides_remote_detection(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware.backend = "mxmaca"
        config.hardware.fields = {"python_version": "configured-python"}
        runner = FakeDetectionRunner(
            {
                "hardware_python_platform": ('{"python_version": "3.11.9", "platform": "Linux-test", "os": "linux"}\n', "", 0),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_hardware(config, Path(tmp), command_runner=runner)
        self.assertEqual(info.fields["backend"].value, "mxmaca")
        self.assertEqual(info.fields["backend"].source, "user_config")
        self.assertEqual(info.fields["python_version"].value, "configured-python")
        self.assertEqual(info.fields["python_version"].source, "user_config")

    def test_safe_probe_success_writes_jsonl_and_logs(self) -> None:
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe": ('{"cpu_count": 16}\n', "", 0, False),
                "safe_probe_vector_width_probe": ('{"pointer_bits": 64}\n', "", 0, False),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            records, log_lines = run_safe_probes(runner, results)
            rows = [
                json.loads(line)
                for line in (results / "hardware_probe.jsonl").read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(records), 2)
        self.assertEqual(rows[0]["source"], "safe_probe")
        self.assertEqual(rows[0]["status"], "pass")
        self.assertEqual(rows[0]["candidate"]["cpu_count"], 16)
        self.assertIn("safe probe threads_probe: pass", log_lines)

    def test_safe_probe_failure_timeout_and_exception_do_not_interrupt(self) -> None:
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe": ("", "boom", 1, False),
                "safe_probe_vector_width_probe": RuntimeError("runner exploded"),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            records, _ = run_safe_probes(runner, results)
            stderr_text = (results / "logs" / "hardware_probe" / "vector_width_probe.stderr.log").read_text(encoding="utf-8")
        self.assertEqual([record["status"] for record in records], ["failed", "exception"])
        self.assertIn("runner exploded", stderr_text)

        timeout_runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe": ("", "slow", 124, True),
                "safe_probe_vector_width_probe": ('{"pointer_bits": 64}\n', "", 0, False),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            records, _ = run_safe_probes(timeout_runner, Path(tmp))
        self.assertEqual(records[0]["status"], "timeout")
        self.assertEqual(records[1]["status"], "pass")

    def test_detect_hardware_runs_safe_probe_without_interrupting(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware_detection.remote_detection = False
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe": ('{"cpu_count": 8}\n', "", 0, False),
                "safe_probe_vector_width_probe": ("", "probe failed", 1, False),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            info = detect_hardware(config, results, command_runner=runner)
            rows = [
                json.loads(line)
                for line in (results / "hardware_probe.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            log_text = (results / "hardware_detection.log").read_text(encoding="utf-8")
        self.assertTrue(info.safe_probe_used)
        self.assertEqual(len(info.safe_probe_results), 2)
        self.assertEqual(rows[0]["status"], "pass")
        self.assertEqual(rows[1]["status"], "failed")
        self.assertIn("safe probe vector_width_probe: failed", log_text)

    def test_detection_disabled_skips_safe_probe_but_writes_file(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware_detection.enabled = False
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            info = detect_hardware(config, results, command_runner=FakeProbeRunner({}))
            probe_text = (results / "hardware_probe.jsonl").read_text(encoding="utf-8")
            log_text = (results / "hardware_detection.log").read_text(encoding="utf-8")
        self.assertFalse(info.safe_probe_used)
        self.assertEqual(probe_text, "")
        self.assertIn("skipping safe probe", log_text)


if __name__ == "__main__":
    unittest.main()
