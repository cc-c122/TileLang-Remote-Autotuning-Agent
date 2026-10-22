from __future__ import annotations

import importlib.util
import contextlib
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class PagedAttentionProtocolTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "kernel_opt_agent/samples/paged_attention_decode/run_paged_attention_decode.py"
        spec = importlib.util.spec_from_file_location("paged_protocol_fixture", path)
        self.module = importlib.util.module_from_spec(spec)
        # Unit-test control flow only; never report these stubs as GPU evidence.
        maximum = types.SimpleNamespace(item=lambda: 0.25)
        self.torch = types.SimpleNamespace(abs=lambda value: value, max=lambda value: maximum,
                                           allclose=lambda *args, **kwargs: True)
        with patch.dict(sys.modules, {"torch": self.torch, spec.name: self.module}):
            spec.loader.exec_module(self.module)
        self.shape = self.module.ShapeConfig(1, 32, 8, 128, 16, 128, "fp16")

    def _run(self, mode):
        upstream = types.SimpleNamespace(ref_program_torch_paged=lambda *args: 1)
        inputs = (upstream,) + (None,) * 7
        with patch.object(self.module, "_make_inputs", return_value=inputs), patch.object(
            self.module, "_compiled_kernel_call", return_value=lambda: 1,
        ), patch.object(self.module, "_measure_once", return_value=2.0) as measure:
            result = self.module.run_baseline(self.shape, 3, 5, 0.002, 0.002, mode)
        return result, measure.call_count

    def test_build_does_not_benchmark_or_claim_correctness(self):
        result, count = self._run("build")
        self.assertEqual(count, 0)
        self.assertNotIn("correctness", result)
        self.assertNotIn("benchmark", result)

    def test_correctness_applies_tolerance_before_benchmark(self):
        calls = []
        self.torch.allclose = lambda *args, **kwargs: calls.append(kwargs) or True
        result, count = self._run("correctness")
        self.assertEqual(count, 0)
        self.assertEqual(calls, [{"atol": 0.002, "rtol": 0.002}])
        self.assertEqual(result["correctness"]["max_error"], 0.25)
        self.assertEqual(result["benchmark"]["measurement_runs"], 0)
        self.assertIsNone(result["benchmark"]["stddev_ms"])

    def test_benchmark_preserves_five_single_invocation_measurements(self):
        result, count = self._run("benchmark")
        self.assertEqual(count, 5)
        self.assertEqual(result["benchmark"]["raw_samples_ms"], [2.0] * 5)
        self.assertEqual(result["seed"], 42)

    def test_correctness_failure_prevents_measurements(self):
        self.torch.allclose = lambda *args, **kwargs: False
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(AssertionError, "max_error=0.25"):
            self._run("benchmark")


if __name__ == "__main__":
    unittest.main()
