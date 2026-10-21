from __future__ import annotations

import unittest

from kernel_opt_agent.source_optimizer.performance import CodegenEvidence, assess_performance, parse_codegen_evidence, saved_gate_is_accepted


class PerformanceGateTests(unittest.TestCase):
    def test_same_codegen_blocks_even_a_large_apparent_speedup(self):
        evidence = CodegenEvidence("a" * 64, "available")
        gate = assess_performance([10, 10, 10], [5, 5, 5], 1, evidence, evidence, [10, 10, 10])
        self.assertEqual(gate.decision, "codegen_unchanged")
        self.assertEqual(gate.codegen_status, "unchanged")

    def test_median_gain_inside_noise_is_inconclusive(self):
        gate = assess_performance([8, 10, 12], [7, 9, 11], 1, baseline_recheck=[9, 10, 11])
        self.assertEqual(gate.decision, "inconclusive")
        self.assertAlmostEqual(gate.median_improvement_percent, 10)
        self.assertLess(gate.conservative_improvement_percent, 0)

    def test_post_candidate_control_rejects_time_drift(self):
        gate = assess_performance([10, 10, 10], [8, 8, 8], 5, baseline_recheck=[7, 7, 7])
        self.assertEqual(gate.decision, "inconclusive")
        self.assertFalse(gate.baseline_recheck_passed)

    def test_no_recheck_cannot_publish(self):
        gate = assess_performance([10, 10, 10], [5, 5, 5], 5)
        self.assertEqual(gate.decision, "inconclusive")
        self.assertIn("baseline control", gate.reason)

    def test_stable_improvement_passes_without_profiler_or_codegen(self):
        gate = assess_performance([10, 10.1, 10.2], [8, 8.1, 8.2], 5, baseline_recheck=[9.9, 10, 10.1])
        self.assertEqual(gate.decision, "accepted")
        self.assertTrue(gate.baseline_recheck_passed)
        self.assertEqual(gate.codegen_status, "unavailable")
        self.assertIsNone(gate.baseline_codegen_hash)
        self.assertTrue(any("unavailable" in item for item in gate.uncertainty))

    def test_under_budget_and_invalid_samples_are_not_accepted(self):
        for samples in ([1, 1], [], [1, 0, 1], [1, -1, 1], [1, float("nan"), 1], [1, float("inf"), 1], [1, True, 1]):
            with self.subTest(samples=samples):
                self.assertEqual(assess_performance([10, 10, 10], list(samples), 0).decision, "inconclusive")

    def test_zero_threshold_does_not_accept_equal_latencies(self):
        self.assertEqual(assess_performance([10] * 3, [10] * 3, 0, baseline_recheck=[10] * 3).decision, "no_improvement")

    def test_regression_is_not_a_validation_failure(self):
        self.assertEqual(assess_performance([10] * 3, [12] * 3, 1).decision, "no_improvement")

    def test_changed_codegen_is_not_a_performance_guarantee(self):
        gate = assess_performance([10] * 3, [12] * 3, 1, CodegenEvidence("a" * 64, "available"), CodegenEvidence("b" * 64, "available"))
        self.assertEqual(gate.codegen_status, "changed")
        self.assertEqual(gate.decision, "no_improvement")

    def test_hash_parser_preserves_unknown_and_rejects_ambiguous_records(self):
        self.assertIsNone(parse_codegen_evidence("normal compiler output").sha256)
        digest = "a" * 64
        self.assertEqual(parse_codegen_evidence(f"GENERATED_SOURCE_SHA256={digest}\r\n").sha256, digest)
        for text in ("GENERATED_SOURCE_SHA256=bad", f"GENERATED_SOURCE_SHA256={digest}\nGENERATED_SOURCE_SHA256={'b' * 64}"):
            self.assertEqual(parse_codegen_evidence(text).status, "inconsistent")
            gate = assess_performance([10] * 3, [5] * 3, 1, parse_codegen_evidence(text))
            self.assertEqual(gate.decision, "inconclusive")

    def test_saved_gate_is_recomputed_before_download_acceptance(self):
        gate = assess_performance([10] * 3, [5] * 3, 1, baseline_recheck=[10] * 3).to_dict()
        self.assertTrue(saved_gate_is_accepted(gate))
        self.assertFalse(saved_gate_is_accepted({**gate, "candidate_samples_ms": [11] * 3}))
        self.assertFalse(saved_gate_is_accepted({**gate, "baseline_codegen_hash": "a" * 64, "candidate_codegen_hash": "a" * 64}))
        self.assertFalse(saved_gate_is_accepted({**gate, "baseline_recheck_samples_ms": []}))
        self.assertFalse(saved_gate_is_accepted({**gate, "threshold_percent": None}))
        self.assertFalse(saved_gate_is_accepted({**gate, "threshold_percent": True}))
        self.assertFalse(saved_gate_is_accepted({**gate, "codegen_status": "changed"}))
        changed = {**gate, "codegen_status": "changed", "baseline_codegen_hash": "a" * 64, "candidate_codegen_hash": "b" * 64}
        self.assertTrue(saved_gate_is_accepted(changed))
        self.assertFalse(saved_gate_is_accepted({**changed, "codegen_status": "unavailable"}))


if __name__ == "__main__":
    unittest.main()
