from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.hardware.detector import detect_hardware
from kernel_opt_agent.hardware.profile_loader import HardwareProfileLoader


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertIn("hardware detection failed", info.warnings[0])
        self.assertIn("hardware detection failed", log_text)
        self.assertIn("fields", detected)
        self.assertIsNone(detected["fields"]["total_memory_GB"]["value"])


if __name__ == "__main__":
    unittest.main()
