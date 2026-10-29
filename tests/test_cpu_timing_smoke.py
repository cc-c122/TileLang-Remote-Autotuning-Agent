import unittest
from unittest.mock import patch

from scripts.smoke_source_copy_timing import run_smoke


class CpuTimingSmokeTests(unittest.TestCase):
    def test_smoke_accepts_negative_observed_change_without_a_required_speedup(self):
        clock = []
        for index in range(10):
            clock.extend((float(index), index + (0.001 if index < 5 else 0.002)))
        with patch("scripts.smoke_source_copy_timing.time.perf_counter", side_effect=clock):
            result = run_smoke(size=8)
        self.assertTrue(result["correctness_passed"])
        self.assertLess(result["observed_improvement_percent"], 0)
        self.assertFalse(result["speedup_required"])
        self.assertEqual(len(result["records"]["baseline_loop"]["raw_samples_ms"]), 5)
