from __future__ import annotations

import unittest
from types import SimpleNamespace

from kernel_opt_agent.diagnosis import EvidenceBundle, diagnose_bottlenecks
from kernel_opt_agent.main import collect_profiler_result
from kernel_opt_agent.profiler.base import ProfilerResult
from kernel_opt_agent.profiler.dummy_profiler import DummyProfiler
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.profiler.tilelang_log_profiler import TileLangLogProfiler


class ProfilerDiagnosisTests(unittest.TestCase):
    def assert_diagnosis_shape(self, records):
        self.assertEqual(len(records), 10)
        for record in records:
            data = record.to_dict()
            self.assertIn("bottleneck_type", data)
            self.assertIn("confidence", data)
            self.assertIn("evidence", data)
            self.assertIn("uncertainty", data)
            self.assertIn("recommended_actions", data)

    def test_no_profiler_keeps_metrics_none(self):
        result = ProfilerResult.empty()
        self.assertIsNone(result.latency)
        self.assertFalse(result.available_metrics["latency"])
        diagnoses = diagnose_bottlenecks(EvidenceBundle(result))
        self.assert_diagnosis_shape(diagnoses)
        self.assertTrue(any(item.uncertainty for item in diagnoses))

    def test_dummy_profiler_parses_benchmark_only(self):
        result = DummyProfiler().collect(
            {"benchmark_stdout": 'BENCHMARK_RESULT latency_ms=1.25 tflops=4.5 bandwidth_gbps=678.0 reason="ok"'}
        )
        self.assertEqual(result.latency, 1.25)
        self.assertEqual(result.tflops, 4.5)
        self.assertEqual(result.estimated_hbm_bandwidth, 678.0)
        self.assertIsNone(result.register_count)
        self.assertTrue(result.available_metrics["latency"])
        self.assertFalse(result.available_metrics["register_count"])

    def test_tilelang_profiler_parses_benchmark_and_logs(self):
        result = TileLangLogProfiler().collect(
            {
                "benchmark_stdout": "BENCHMARK_RESULT latency_ms=2.0 tflops=3.0 bandwidth_gbps=400.0",
                "benchmark_stderr": "",
                "compile_log": (
                    "register_count=64 shared_memory_bytes=49152 private_memory_bytes=128 "
                    "occupancy=0.33 shared_bank_conflict=2 memory_coalescing_efficiency=0.55"
                ),
                "generated_code": "",
            }
        )
        self.assertEqual(result.register_count, 64)
        self.assertEqual(result.shared_memory_bytes, 49152)
        self.assertEqual(result.private_memory_bytes, 128)
        diagnoses = diagnose_bottlenecks(EvidenceBundle(result))
        by_type = {item.bottleneck_type: item for item in diagnoses}
        self.assertTrue(by_type["private_memory_spill"].evidence)
        self.assertTrue(by_type["low_occupancy"].evidence)
        self.assertTrue(by_type["poor_memory_coalescing"].evidence)

    def test_mxmaca_profiler_missing_fields_are_not_fabricated(self):
        result = MxmacaProfiler().collect({"benchmark_stdout": "no metrics here", "profiler_stdout": "TODO"})
        self.assertIsNone(result.latency)
        self.assertIsNone(result.hbm_read_bandwidth)
        self.assertFalse(result.available_metrics["hbm_read_bandwidth"])
        diagnoses = diagnose_bottlenecks(EvidenceBundle(result))
        self.assert_diagnosis_shape(diagnoses)

    def test_profiler_failure_is_recorded_not_raised(self):
        config = SimpleNamespace(profiler=SimpleNamespace(enabled=True, type="unsupported"))
        record, result = collect_profiler_result(config, {}, "", "", "")
        self.assertTrue(record["enabled"])
        self.assertIn("unsupported profiler.type", record["error"])
        self.assertIsNone(result.latency)


if __name__ == "__main__":
    unittest.main()
