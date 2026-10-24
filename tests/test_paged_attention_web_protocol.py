from __future__ import annotations

import importlib.util
import contextlib
import hashlib
import io
import sys
import tempfile
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

    def test_profile_runs_one_operator_without_reference_or_benchmark(self):
        result, count = self._run("profile")
        self.assertEqual(count, 0)
        self.assertEqual(result["operator_calls"], 1)
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
        self.assertEqual(result["benchmark"]["latency_scope"], "operator_end_to_end")
        self.assertTrue(result["benchmark"]["output_allocation_included"])

    def test_build_saves_actual_generated_source_and_its_hash(self):
        # Exercise artifact handling only; this stub is not compiler/GPU evidence.
        run_once = lambda: 1
        run_once.generated_kernel_source = "// compiler fixture\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(self.module, "_make_inputs", return_value=(None,) * 8), patch.object(
                self.module, "_compiled_kernel_call", return_value=run_once,
            ), patch.object(self.module, "Path", side_effect=lambda value: root / value), contextlib.redirect_stdout(io.StringIO()):
                result = self.module.run_baseline(self.shape, 3, 5, 0.002, 0.002, "build")
            saved = root / result["generated_source_path"]
            self.assertEqual(saved.read_text(encoding="utf-8"), run_once.generated_kernel_source)
            self.assertEqual(result["generated_source_sha256"], hashlib.sha256(saved.read_bytes()).hexdigest())

    def test_correctness_failure_prevents_measurements(self):
        self.torch.allclose = lambda *args, **kwargs: False
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(AssertionError, "max_error=0.25"):
            self._run("benchmark")

    def test_http_failure_records_response_without_credentials(self):
        import httpx
        from scripts.smoke_paged_attention_web import run_smoke

        secret = "smoke-password-sentinel"
        session = "smoke-session-sentinel"

        def respond(request):
            if request.url.path == "/api/ssh/sessions":
                return httpx.Response(200, json={"session_id": session})
            if request.url.path == "/api/hardware/resolve":
                return httpx.Response(200, json={"selected_device_id": "fixture-device", "devices": []})
            if request.method == "DELETE":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(422, json={"detail": {"message": secret + " " + session}})

        client_type = httpx.Client
        transport = httpx.MockTransport(respond)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("kernel_opt_agent.main.WORKSPACE_ROOT", root), patch(
                "scripts.smoke_paged_attention_web.httpx.Client",
                side_effect=lambda **kwargs: client_type(transport=transport, **kwargs),
            ):
                result = run_smoke("http://127.0.0.1:8876", {"host": "fixture.invalid"}, secret, root / "evidence")
            self.assertEqual(result["failure"]["status"], 422)
            self.assertEqual(result["failure"]["path"], "/api/samples/upload")
            saved = (root / "evidence/web_smoke.json").read_text(encoding="utf-8")
            for value in (secret, session):
                self.assertNotIn(value, saved)

    def test_source_runtime_prefix_reaches_normal_web_task_commands(self):
        import httpx
        from scripts.smoke_paged_attention_web import run_smoke

        submitted = []
        prefix = "env PYTHONPATH=/app/tilelang-metax python3 -B"

        def respond(request):
            path = request.url.path
            if path == "/api/ssh/sessions":
                return httpx.Response(200, json={"session_id": "fixture-session"})
            if path == "/api/hardware/resolve":
                return httpx.Response(200, json={"selected_device_id": "fixture-device", "devices": []})
            if path == "/api/samples/upload":
                return httpx.Response(200, json={"upload_id": "fixture-upload"})
            if path == "/api/tasks":
                import json
                submitted.append(json.loads(request.content))
                return httpx.Response(200, json={"task_id": "fixture-task"})
            if path.endswith("/results"):
                return httpx.Response(200, json={"results": {"results_dir": "fixture-results"}})
            if path.endswith("/events"):
                return httpx.Response(200, json={"events": []})
            return httpx.Response(200, json={"task": {"status": "failed", "error": "fixture build failure",
                                                       "execution_mode": "source_optimization"}})

        client_type = httpx.Client
        transport = httpx.MockTransport(respond)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("kernel_opt_agent.main.WORKSPACE_ROOT", root), patch(
                "scripts.smoke_paged_attention_web.httpx.Client",
                side_effect=lambda **kwargs: client_type(transport=transport, **kwargs),
            ):
                result = run_smoke("http://127.0.0.1:8876", {}, "fixture-password", root, command_prefix=prefix,
                                   profiler_options={"command": "python3 profile.py --context-length {context_length}"})
        self.assertEqual(len(submitted), 2)
        for payload in submitted:
            self.assertEqual(payload["runner"]["type"], "ssh")
            self.assertTrue(payload["optimization"]["enabled"])
            for command in payload["commands"].values():
                self.assertTrue(command.startswith(prefix + " run_paged_attention_decode.py "))
            context = payload["project_name"].rsplit("ctx", 1)[1]
            self.assertEqual(payload["profiler"]["command"], "python3 profile.py --context-length " + context)
        self.assertEqual(result["tasks"][0]["error"], "fixture build failure")


if __name__ == "__main__":
    unittest.main()
