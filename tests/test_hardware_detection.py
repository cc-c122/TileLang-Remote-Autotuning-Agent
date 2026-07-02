from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.hardware.hardware_info import HardwareInfo
from kernel_opt_agent.hardware.safe_probe import run_safe_probes
from kernel_opt_agent.runner.local_runner import CommandResult, LocalRunner
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
    def __init__(self, responses, probe_runner_mode: str = "remote_probe"):
        self.responses = responses
        self.probe_runner_mode = probe_runner_mode
        self.commands: list[str] = []
        self.command_texts: list[str] = []

    def run(self, name, command):
        self.commands.append(name)
        self.command_texts.append(command or "")
        response = self.responses.get(name)
        if isinstance(response, Exception):
            raise response
        stdout, stderr, returncode, timeout, guard_denied = response or ("{}", "", 0, False, False)
        now = time.time()
        return CommandResult(name, command or "", returncode, stdout, stderr, now, now, 0.0, timeout=timeout, guard_denied=guard_denied, error_message=stderr if guard_denied else None)


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
        search_space = {"NUM_THREADS": [128, 256], "VECTOR_WIDTH": [1, 4], "NUM_STAGES": [2]}
        runner = FakeProbeRunner({}, probe_runner_mode="ssh_probe")
        for name in [
            "safe_probe_threads_probe_128",
            "safe_probe_threads_probe_256",
            "safe_probe_vector_width_probe_1",
            "safe_probe_vector_width_probe_4",
            "safe_probe_stages_probe_2",
            "safe_probe_shared_memory_probe_32768",
            "safe_probe_shared_memory_probe_49152",
            "safe_probe_shared_memory_probe_65536",
            "safe_probe_shared_memory_probe_98304",
            "safe_probe_shared_memory_probe_131072",
        ]:
            runner.responses[name] = ('SAFE_PROBE_RESULT status=PASS reason="ok"\n', "", 0, False, False)
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            records, log_lines = run_safe_probes(runner, results, search_space, 60, None)
            rows = [
                json.loads(line)
                for line in (results / "hardware_probe.jsonl").read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(records), 10)
        self.assertEqual(rows[0]["source"], "safe_probe")
        self.assertEqual(rows[0]["status"], "pass")
        self.assertEqual(rows[0]["param_name"], "NUM_THREADS")
        self.assertEqual(rows[0]["candidate_value"], 128)
        self.assertEqual([r["candidate_value"] for r in rows if r["probe_name"] == "threads_probe"], [128, 256])
        self.assertEqual([r["candidate_value"] for r in rows if r["probe_name"] == "vector_width_probe"], [1, 4])
        self.assertEqual([r["candidate_value"] for r in rows if r["probe_name"] == "stages_probe"], [2])
        self.assertIn("safe probe threads_probe NUM_THREADS=128: pass", log_lines)
        self.assertIn("@tilelang.jit", runner.command_texts[0])
        self.assertIn("T.Kernel", runner.command_texts[0])
        self.assertIn("T.alloc_shared", runner.command_texts[-1])
        self.assertIn("T.Pipelined", runner.command_texts[0])
        vector_width_4_script = runner.command_texts[3]
        shared_memory_32k_script = runner.command_texts[5]
        self.assertIn("VECTOR_WIDTH = 4", vector_width_4_script)
        self.assertIn("SHARED_ELEMS = 4", vector_width_4_script)
        self.assertIn("SHARED_ELEMS = 8192", shared_memory_32k_script)

    def test_remote_safe_probe_skips_when_tilelang_unavailable(self) -> None:
        search_space = {"NUM_THREADS": [128], "VECTOR_WIDTH": [], "NUM_STAGES": []}
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe_128": ('SAFE_PROBE_RESULT status=SKIPPED reason="TileLang unavailable"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_32768": ('SAFE_PROBE_RESULT status=SKIPPED reason="TileLang unavailable"\n', "", 0, False, False),
            },
            probe_runner_mode="ssh_probe",
        )
        with tempfile.TemporaryDirectory() as tmp:
            records, _ = run_safe_probes(runner, Path(tmp), search_space, 60, None)
        self.assertEqual([record["status"] for record in records], ["skipped", "skipped"])
        self.assertTrue(all(record["confidence"] == "low" for record in records))
        self.assertIn("TileLang unavailable", records[0]["inference"])

    def test_remote_safe_probe_generates_mxmaca_script(self) -> None:
        search_space = {"NUM_THREADS": [128], "VECTOR_WIDTH": [], "NUM_STAGES": []}
        hardware_info = HardwareInfo.unknown()
        hardware_info.set_field("backend", "mxmaca", "user_config", "high", "test backend")
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe_128": ('SAFE_PROBE_RESULT status=PASS reason="threads_probe mxmaca/metax small kernel compiled and ran for NUM_THREADS=128"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_32768": ('SAFE_PROBE_RESULT status=PASS reason="shared_memory_probe mxmaca/metax small kernel compiled and ran for SHARED_MEMORY_BYTES=32768"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_49152": ('SAFE_PROBE_RESULT status=PASS reason="shared_memory_probe mxmaca/metax small kernel compiled and ran for SHARED_MEMORY_BYTES=49152"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_65536": ('SAFE_PROBE_RESULT status=PASS reason="shared_memory_probe mxmaca/metax small kernel compiled and ran for SHARED_MEMORY_BYTES=65536"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_98304": ('SAFE_PROBE_RESULT status=PASS reason="shared_memory_probe mxmaca/metax small kernel compiled and ran for SHARED_MEMORY_BYTES=98304"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_131072": ('SAFE_PROBE_RESULT status=PASS reason="shared_memory_probe mxmaca/metax small kernel compiled and ran for SHARED_MEMORY_BYTES=131072"\n', "", 0, False, False),
            },
            probe_runner_mode="ssh_probe",
        )
        with tempfile.TemporaryDirectory() as tmp:
            records, _ = run_safe_probes(runner, Path(tmp), search_space, 60, hardware_info)
        self.assertTrue(records)
        self.assertTrue(all(record["status"] == "pass" for record in records))
        self.assertIn("mxmaca/metax small kernel compiled and ran", records[0]["inference"])
        self.assertIn("BACKEND = 'mxmaca'", runner.command_texts[0])
        self.assertIn("IS_MXMACA = BACKEND in", runner.command_texts[0])
        self.assertIn("import mctilelang as tilelang", runner.command_texts[0])
        self.assertIn("DEVICE = \"cuda\"", runner.command_texts[0])

    def test_remote_safe_probe_skips_when_mctilelang_unavailable(self) -> None:
        search_space = {"NUM_THREADS": [128], "VECTOR_WIDTH": [], "NUM_STAGES": []}
        hardware_info = HardwareInfo.unknown()
        hardware_info.set_field("backend", "mxmaca", "user_config", "high", "test backend")
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe_128": ('SAFE_PROBE_RESULT status=SKIPPED reason="mcTileLang unavailable for mxmaca/metax probe: no module named mctilelang"\n', "", 0, False, False),
                "safe_probe_shared_memory_probe_32768": ('SAFE_PROBE_RESULT status=SKIPPED reason="mcTileLang unavailable for mxmaca/metax probe: no module named mctilelang"\n', "", 0, False, False),
            },
            probe_runner_mode="ssh_probe",
        )
        with tempfile.TemporaryDirectory() as tmp:
            records, _ = run_safe_probes(runner, Path(tmp), search_space, 60, hardware_info)
        self.assertEqual([record["status"] for record in records], ["skipped", "skipped"])
        self.assertTrue(all(record["confidence"] == "low" for record in records))
        self.assertIn("mcTileLang unavailable for mxmaca/metax probe", records[0]["inference"])

    def test_safe_probe_failure_timeout_and_exception_do_not_interrupt(self) -> None:
        search_space = {"NUM_THREADS": [128], "VECTOR_WIDTH": [4], "NUM_STAGES": [2]}
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe_128": ("", "boom", 1, False, False),
                "safe_probe_vector_width_probe_4": RuntimeError("runner exploded"),
                "safe_probe_stages_probe_2": ("", "denied", 126, False, True),
                "safe_probe_shared_memory_probe_32768": ("", "slow", 124, True, False),
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            records, _ = run_safe_probes(runner, results, search_space, 60, None)
            stderr_text = (results / "logs" / "hardware_probe" / "vector_width_probe_4.stderr.log").read_text(encoding="utf-8")
        self.assertEqual([record["status"] for record in records], ["failed", "exception", "guard_denied", "timeout"])
        self.assertIn("runner exploded", stderr_text)
        self.assertNotIn("safe_probe_shared_memory_probe_49152", runner.commands)

    def test_safe_probe_local_runner_is_low_confidence_mock(self) -> None:
        search_space = {"NUM_THREADS": [128], "VECTOR_WIDTH": [1], "NUM_STAGES": [2]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = LocalRunner(root, timeout_seconds=5)
            records, _ = run_safe_probes(runner, root / "results", search_space, 5, None)
        self.assertTrue(records)
        self.assertTrue(all(record["confidence"] == "low" for record in records))
        self.assertTrue(all("runner_mode=local_mock" in record["inference"] for record in records))
        self.assertTrue(any("real GPU capability was not verified" in record["inference"] for record in records))

    def test_detect_hardware_runs_safe_probe_without_interrupting(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware_detection.remote_detection = False
        runner = FakeProbeRunner(
            {
                "safe_probe_threads_probe_128": ("", "probe failed", 1, False, False),
                "safe_probe_shared_memory_probe_32768": ("", "shared memory failed", 1, False, False),
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
        self.assertGreater(len(info.safe_probe_results), 2)
        self.assertEqual(rows[0]["probe_name"], "threads_probe")
        self.assertEqual(rows[0]["candidate_value"], 128)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertIn("safe probe threads_probe NUM_THREADS=128: failed", log_text)

    def test_conservative_mode_only_when_critical_fields_unknown(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        config.hardware_detection.remote_detection = False
        config.hardware_detection.safe_probe = False
        config.hardware.fields = {
            "max_threads_per_block": 1024,
            "shared_memory_per_block_bytes": 98304,
            "vector_alignment_bytes": 16,
            "warp_size": 32,
            "supported_dtypes": ["float16"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_hardware(config, Path(tmp), command_runner=FakeProbeRunner({}))
        self.assertFalse(info.conservative_mode)

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
