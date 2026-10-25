from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from kernel_opt_agent.profiler.mcprofiler import McProfilerCollectionRequest, collect_remote_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.target_selection import generated_kernel_symbols, resolve_kernel_target
from kernel_opt_agent.source_optimizer.engine import _collect_source_profiler
from tests import test_mcprofiler_attribution as attribution_tests
from tests.test_mcprofiler_collection import FakeRemoteCollectionRunner
from tests.test_source_optimizer import _config, _source


# Synthetic generated-code scaffolding; native metric fixture values are not
# modified and none of these tests constitutes GPU performance evidence.
SOURCE = '''
#include <tl_templates/maca/gemm.h>
extern "C" __global__ void decode_x17(float* out);
extern "C" __global__ void __launch_bounds__(128, 1) decode_x17(float* out) {
  if (out) { out[0] = __builtin_mxc_mma_16x16x16f16(1, 2, 3); }
}
extern "C" __global__ void reduce_aux(float* out) { out[0] = 0; }
'''


def build_log(source=SOURCE, path="results/generated.cc", digest=None):
    digest = digest or hashlib.sha256(source.encode()).hexdigest()
    return "GENERATED_SOURCE_SHA256=" + digest + "\nBUILD_RESULT " + json.dumps({
        "generated_source_path": path, "generated_source_sha256": digest,
    })


class MixedKernelRunner(FakeRemoteCollectionRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        artifact = self.remote_root / "results/generated.cc"
        artifact.parent.mkdir()
        artifact.write_text(SOURCE, encoding="utf-8", newline="\n")
        self.downloads = []

    def run(self, name, command):
        if name == "build":
            return self._result(name, command, stdout=build_log())
        return super().run(name, command)

    def run_cancellable(self, name, command, should_cancel=None, **kwargs):
        result = super().run_cancellable(name, command, should_cancel, **kwargs)
        if name == "mcprofiler_discovery":
            return result
        report = next(self.remote_root.rglob("report_dumped_result.json"))
        for filename in ("1_torch_fill_dumped_result.json", "9_decode_x17_dumped_result.json", "10_reduce_aux_dumped_result.json"):
            shutil.copy2(report, report.with_name(filename))
        return result

    def download(self, relative_path, local_path, **kwargs):
        self.downloads.append((relative_path, kwargs))
        return super().download(relative_path, local_path, **kwargs)


class McProfilerTargetSelectionTests(unittest.TestCase):
    def request(self, **kwargs):
        return McProfilerCollectionRequest(
            "python3 benchmark.py", "automatic-target", "source-baseline", "a" * 64,
            per_kernel=True, build_log=build_log(), **kwargs,
        )

    def test_compiler_definitions_ignore_prototypes_comments_literals_and_headers(self):
        symbols, matrix = generated_kernel_symbols(SOURCE + '''
// __global__ void fake() { __builtin_mxc_mma_fake(); }
/* __global__ void another() { } */
const char* s = "__global__ void fake2() { __builtin_mxc_mma_x(); }";
''')
        self.assertEqual(symbols, ["decode_x17", "reduce_aux"])
        self.assertEqual(matrix, ["decode_x17"])
        renamed = SOURCE.replace("decode_x17", "arbitrary_runtime_symbol")
        self.assertEqual(generated_kernel_symbols(renamed)[1], ["arbitrary_runtime_symbol"])
        self.assertEqual(generated_kernel_symbols('__global__ void incomplete() {'), ([], []))

    def test_normal_web_trial_passes_build_evidence_and_selects_without_kernel_hint(self):
        with tempfile.TemporaryDirectory() as temporary:
            record, runner, _ = attribution_tests.McProfilerAttributionTests()._normal_trial(Path(temporary), MixedKernelRunner)
            collection = record["profiler"]["collection"]
            self.assertEqual(collection["status"], "collected")
            self.assertEqual(collection["primary_report"], "9_decode_x17_dumped_result.json")
            selection = collection["execution_manifest"]["target_selection"]
            self.assertEqual(selection["mode"], "automatic")
            self.assertEqual(selection["reason"], "unique_matrix_intrinsic_kernel")
            self.assertEqual(selection["compiled_kernel_names"], ["decode_x17", "reduce_aux"])
            command = next(cmd for name, cmd in runner.commands if name == "mcprofiler_collection")
            self.assertIn("--kernelnames decode_x17", command)
            self.assertTrue(all(not item.get("planning_eligible") for item in record["metric_observations"]))
            self.assertEqual(collection["execution_manifest"]["target_attribution"]["status"], "unverified")

    def test_source_optimizer_passes_its_own_build_log_to_automatic_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample"
            sample.mkdir()
            (sample / "kernel.py").write_text(_source(), encoding="utf-8")
            cfg = _config(sample)
            cfg.profiler.enabled, cfg.profiler.type, cfg.profiler.auto_collect = True, "mxmaca", True
            runner = MixedKernelRunner(root / "remote")
            outcome = SimpleNamespace(status="benchmark_ok", runner=runner, logs={"build": build_log()})
            result = _collect_source_profiler(cfg, root / "results", outcome, "source-baseline", "a" * 64, lambda: False, None)
            self.assertEqual(result["status"], "collected")
            self.assertEqual(result["kernel_names"], ["decode_x17"])
            self.assertIn("structural preference", result["warnings"][-1])

    def test_single_compiled_elementwise_kernel_is_selected(self):
        source = '__global__ void custom_elementwise(float* out) { out[0]=0; }'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MixedKernelRunner(root / "remote")
            (runner.remote_root / "results/generated.cc").write_bytes(source.encode())
            selected = resolve_kernel_target(runner, root / "results", "single", build_log(source).replace("\n", "\r\n"), ())
            self.assertEqual(selected["reason"], "single_compiled_kernel")
            self.assertEqual(selected["selected_kernel_names"], ["custom_elementwise"])

    def test_ambiguous_matrix_kernels_or_no_matrix_target_are_not_guessed(self):
        for source in (SOURCE.replace("out[0] = 0", "out[0] = __builtin_mxc_mma_other(1)"),
                       SOURCE.replace("__builtin_mxc_mma_16x16x16f16", "ordinary_helper")):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runner = MixedKernelRunner(root / "remote")
                (runner.remote_root / "results/generated.cc").write_bytes(source.encode())
                result = collect_remote_mcprofiler_case(runner, root / "results", replace(self.request(), build_log=build_log(source)))
                self.assertEqual((result.status, result.reason_category), ("failed", "target_selection_failed"))
                self.assertEqual(result.execution_manifest["target_selection"]["reason"], "ambiguous_compiled_kernels")
                self.assertFalse(any(name == "mcprofiler_collection" for name, _ in runner.commands))

    def test_explicit_override_has_priority_and_does_not_download_generated_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MixedKernelRunner(root / "remote")
            result = collect_remote_mcprofiler_case(runner, root / "results", self.request(kernel_names=("reduce_aux",)))
            self.assertEqual(result.primary_report, "10_reduce_aux_dumped_result.json")
            self.assertEqual(result.execution_manifest["target_selection"]["mode"], "explicit")
            self.assertFalse(any(path == "results/generated.cc" for path, _ in runner.downloads))

    def test_unsafe_missing_inconsistent_or_changed_artifact_fails_closed(self):
        for log, reason in (
            (build_log(path="/etc/source.cc"), "invalid_generated_source_record"),
            (build_log(path="../source.cc"), "invalid_generated_source_record"),
            (build_log(path=".ssh/source.cc"), "invalid_generated_source_record"),
            (build_log(digest="b" * 64), "generated_source_unverified"),
            (build_log() + "\n" + build_log(path="results/other.cc"), "invalid_generated_source_record"),
            (build_log(path="results/missing.cc"), "generated_source_unverified"),
        ):
            with self.subTest(reason=reason, log=log), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runner = MixedKernelRunner(root / "remote")
                selected = resolve_kernel_target(runner, root / "results", "invalid", log, ())
                self.assertEqual((selected["status"], selected["reason"]), ("invalid", reason))
                self.assertFalse(list((root / "results").rglob("generated_source.cc")))

    def test_sensitive_source_is_removed_and_not_serialized(self):
        secret = "synthetic-target-credential-value"
        source = SOURCE + "\n// " + secret
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MixedKernelRunner(root / "remote")
            (runner.remote_root / "results/generated.cc").write_bytes(source.encode())
            selected = resolve_kernel_target(runner, root / "results", "secret", build_log(source), (), secret_values=(secret,))
            self.assertEqual(selected["status"], "invalid")
            self.assertNotIn(secret, json.dumps(selected))
            self.assertFalse(list((root / "results").rglob("generated_source.cc")))

    def test_cancel_during_bounded_artifact_download_stops_before_collection(self):
        cancelled = False

        class CancellingRunner(MixedKernelRunner):
            def download(self, *args, **kwargs):
                nonlocal cancelled
                super().download(*args, **kwargs)
                cancelled = True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = CancellingRunner(root / "remote")
            result = collect_remote_mcprofiler_case(runner, root / "results", self.request(), should_cancel=lambda: cancelled)
            self.assertEqual(result.status, "cancelled")
            self.assertFalse(any(name == "mcprofiler_collection" for name, _ in runner.commands))
            self.assertEqual(runner.downloads[0][1], {"max_files": 1, "max_bytes": 2 * 1024 * 1024})

    def test_no_codegen_record_does_not_guess_among_multiple_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MixedKernelRunner(root / "remote")
            result = collect_remote_mcprofiler_case(runner, root / "results", replace(self.request(), build_log=""))
            self.assertEqual((result.status, result.reason_category), ("failed", "target_report_not_found"))
            self.assertIsNone(result.primary_report)


if __name__ == "__main__":
    unittest.main()
