from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kernel_opt_agent.kernel.variant_generator import VariantGenerator


class BaselineSourceBytesTests(unittest.TestCase):
    def test_plain_sources_preserve_bytes_and_dependencies(self) -> None:
        for source in (
            b"VALUE = 7\n",
            b"VALUE = 7\r\n",
            b"VALUE = 7",
            "# \u4fdd\u7559\u539f\u59cb\u6e90\u7801\nVALUE = 7\n".encode("utf-8"),
            b"# mixed endings\r\nVALUE = 7\n",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                sample = root / "sample"
                sample.mkdir()
                (sample / "kernel.py").write_bytes(source)
                protected = b"assert VALUE == 7\r\n"
                (sample / "correctness.py").write_bytes(protected)
                generator = VariantGenerator(sample, "kernel.py", {}, root / "generated", root / "patches", ["*.py"])
                paths = generator.create_trial(0, 0, {})
                for key in ("kernel", "kernel_copy"):
                    self.assertEqual(paths[key].read_bytes(), source)
                    self.assertEqual(hashlib.sha256(paths[key].read_bytes()).digest(), hashlib.sha256(source).digest())
                self.assertEqual((paths["trial_dir"] / "correctness.py").read_bytes(), protected)
                self.assertEqual((sample / "kernel.py").read_bytes(), source)
                self.assertEqual(paths["patch"].read_bytes(), b"")

    def test_template_patch_exists_before_rendered_file_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample"
            sample.mkdir()
            (sample / "kernel.py").write_text("BM = {{BM}}\n", encoding="utf-8")
            generator = VariantGenerator(sample, "kernel.py", {"BM": [16]}, root / "generated", root / "patches", ["*.py"])
            expected_patch = root / "patches" / "kernel_iter000_cand000.patch"
            original_write = Path.write_text
            calls = []

            def checked_write(path, text, *args, **kwargs):
                if root / "generated" in path.parents:
                    self.assertTrue(expected_patch.is_file())
                    calls.append(path)
                return original_write(path, text, *args, **kwargs)

            with patch.object(Path, "write_text", checked_write):
                paths = generator.create_trial(0, 0, {"BM": 16})
            self.assertEqual(len(calls), 2)
            self.assertIn("BM = 16", paths["kernel"].read_text(encoding="utf-8"))
            self.assertIn("+BM = 16", paths["patch"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
