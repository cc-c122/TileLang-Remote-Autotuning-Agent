from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from kernel_opt_agent.server.app import app


INLINE_KERNEL = """BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}

def kernel_score():
    # BEGIN_AGENT_PATCH: compute
    value = BM + BN + BK + NUM_THREADS + NUM_STAGES + VECTOR_WIDTH
    # END_AGENT_PATCH
    return value
"""


class FastApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "service": "tilelang-agent", "mode": "local-runner"})

    def test_settings_are_redacted_and_plaintext_secrets_rejected(self) -> None:
        old_password = os.environ.get("KERNEL_AGENT_TEST_PASSWORD_EXISTS")
        old_key = os.environ.get("KERNEL_AGENT_TEST_API_KEY_EXISTS")
        os.environ["KERNEL_AGENT_TEST_PASSWORD_EXISTS"] = "secret-password"
        os.environ.pop("KERNEL_AGENT_TEST_API_KEY_EXISTS", None)
        payload = {
            "ssh": {
                "host": "example.invalid",
                "port": 22,
                "username": "user",
                "auth_type": "key",
                "key_path": "~/.ssh/id_rsa",
                "password_env": "KERNEL_AGENT_TEST_PASSWORD_EXISTS",
                "remote_workspace": "/tmp/kernel-agent",
            },
            "llm": {
                "provider": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "api_key_env": "KERNEL_AGENT_TEST_API_KEY_EXISTS",
            },
        }
        try:
            response = self.client.post("/api/settings", json=payload)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["settings"]["ssh"]["key_path"], "<redacted:key_path>")
            self.assertTrue(body["settings"]["ssh"]["password_env_exists"])
            self.assertFalse(body["settings"]["llm"]["api_key_env_exists"])
            dumped = str(body).lower()
            self.assertNotIn("secret-password", dumped)
            rejected = self.client.post("/api/settings", json={"ssh": {"password": "secret"}})
            self.assertEqual(rejected.status_code, 422)
        finally:
            if old_password is None:
                os.environ.pop("KERNEL_AGENT_TEST_PASSWORD_EXISTS", None)
            else:
                os.environ["KERNEL_AGENT_TEST_PASSWORD_EXISTS"] = old_password
            if old_key is None:
                os.environ.pop("KERNEL_AGENT_TEST_API_KEY_EXISTS", None)
            else:
                os.environ["KERNEL_AGENT_TEST_API_KEY_EXISTS"] = old_key

    def test_inline_local_task_runs_to_results(self) -> None:
        request = {
            "project_name": "api-inline-demo",
            "sample": {
                "source_type": "inline",
                "inline_text": INLINE_KERNEL,
                "entry_file": "kernel.py",
            },
            "target": {"gpu_model": "unknown", "backend": "unknown", "user_overrides": {}},
            "commands": {
                "build_command": "python -m py_compile kernel.py",
                "correctness_command": "python -c \"import kernel; print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"",
                "benchmark_command": "python -c \"import kernel; print(f'BENCHMARK_RESULT latency_ms={10.0/kernel.kernel_score():.4f} tflops=1.0 bandwidth_gbps=2.0')\"",
            },
            "budget": {"strategy": "rule_based", "max_iterations": 1, "candidates_per_iteration": 1, "timeout_seconds": 30, "objective": "latency"},
            "profiler": {"enabled": True, "type": "dummy"},
            "patching": {"enabled": True, "run_controlled_trial": True},
        }
        created = self.client.post("/api/tasks", json=request)
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task_id"]
        status = None
        for _ in range(80):
            status_response = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(status_response.status_code, 200)
            status = status_response.json()["task"]["status"]
            if status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        self.assertEqual(status, "completed")
        results = self.client.get(f"/api/tasks/{task_id}/results")
        self.assertEqual(results.status_code, 200)
        payload = results.json()["results"]
        task = results.json()["task"]
        self.assertEqual(task["project_name"], "api-inline-demo")
        for field in {
            "current_iteration",
            "total_trials",
            "best_latency",
            "baseline_latency",
            "improvement_percent",
            "current_stage",
            "latest_message",
        }:
            self.assertIn(field, task)
        self.assertGreaterEqual(task["total_trials"], 1)
        self.assertIsNotNone(task["baseline_latency"])
        self.assertIsNotNone(task["best_latency"])
        self.assertTrue((Path(task["workspace"]) / "run_request.yaml").exists())
        effective = Path(task["workspace"]) / "effective_config.yaml"
        self.assertTrue(effective.exists())
        self.assertNotIn("password:", effective.read_text(encoding="utf-8").lower())
        self.assertNotIn("api_key:", effective.read_text(encoding="utf-8").lower())
        self.assertTrue(payload["generated_files"]["experiments.jsonl"]["exists"])
        self.assertTrue(payload["generated_files"]["summary.csv"]["exists"])
        self.assertTrue(payload["generated_files"]["profiler_results.jsonl"]["exists"])
        self.assertTrue(payload["generated_files"]["diagnosis.jsonl"]["exists"])
        self.assertTrue(payload["generated_files"]["patch_trials.jsonl"]["exists"])
        self.assertIsInstance(payload["summary_table"], list)
        self.assertIsInstance(payload["failed_cases"], list)
        self.assertIn("def kernel_score", payload["best_kernel"])
        self.assertIn("## Patch Trials", payload["report_markdown"])
        self.assertIsNotNone(payload["best_config"])
        self.assertIsNotNone(payload["improvement_percent"])
        events = self.client.get(f"/api/tasks/{task_id}/events").json()["events"]
        event_types = {event["type"] for event in events}
        for required in {
            "task_created",
            "hardware_resolved",
            "sample_uploaded",
            "build_started",
            "correctness_started",
            "benchmark_started",
            "trial_completed",
            "best_updated",
            "report_generated",
            "task_completed",
        }:
            self.assertIn(required, event_types)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/download/best_kernel").status_code, 200)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/download/report").status_code, 200)
        task_list = self.client.get("/api/tasks")
        self.assertEqual(task_list.status_code, 200)
        tasks = task_list.json()["tasks"]
        listed = next(item for item in tasks if item["task_id"] == task_id)
        self.assertEqual(listed["project_name"], "api-inline-demo")
        self.assertEqual(listed["status"], "completed")
        self.assertIn("latest_message", listed)
        self.assertIn("improvement_percent", listed)
        self.assertNotIn("password", str(listed).lower())
        self.assertNotIn("api_key", str(listed).lower())

    def test_cancel_marks_task_cancelled_and_partial_results_remain_readable(self) -> None:
        request = {
            "project_name": "api-cancel-demo",
            "sample": {
                "source_type": "inline",
                "inline_text": INLINE_KERNEL,
                "entry_file": "kernel.py",
            },
            "target": {"gpu_model": "unknown", "backend": "unknown", "user_overrides": {}},
            "commands": {
                "build_command": "python -m py_compile kernel.py",
                "correctness_command": "python -c \"print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"",
                "benchmark_command": "python -c \"import time; time.sleep(2); print('BENCHMARK_RESULT latency_ms=1.0 tflops=1.0 bandwidth_gbps=1.0')\"",
            },
            "budget": {"strategy": "rule_based", "max_iterations": 2, "candidates_per_iteration": 1, "timeout_seconds": 10, "objective": "latency"},
            "profiler": {"enabled": False, "type": "dummy"},
            "patching": {"enabled": False, "run_controlled_trial": False},
        }
        created = self.client.post("/api/tasks", json=request)
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task_id"]
        time.sleep(0.3)
        cancelled = self.client.post(f"/api/tasks/{task_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        status = None
        for _ in range(80):
            status = self.client.get(f"/api/tasks/{task_id}").json()["task"]["status"]
            if status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        self.assertEqual(status, "cancelled")
        events = self.client.get(f"/api/tasks/{task_id}/events").json()["events"]
        event_types = {event["type"] for event in events}
        self.assertIn("task_cancelled", event_types)
        results = self.client.get(f"/api/tasks/{task_id}/results")
        self.assertEqual(results.status_code, 200)
        self.assertIn("generated_files", results.json()["results"])

    def test_second_task_waits_for_serial_worker_slot(self) -> None:
        request = {
            "project_name": "api-serial-demo",
            "sample": {"source_type": "inline", "inline_text": INLINE_KERNEL, "entry_file": "kernel.py"},
            "commands": {
                "build_command": "python -m py_compile kernel.py",
                "correctness_command": "python -c \"print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"",
                "benchmark_command": "python -c \"import time; time.sleep(1); print('BENCHMARK_RESULT latency_ms=1.0 tflops=1.0 bandwidth_gbps=1.0')\"",
            },
            "budget": {"strategy": "rule_based", "max_iterations": 0, "candidates_per_iteration": 1, "timeout_seconds": 10, "objective": "latency"},
        }
        first = self.client.post("/api/tasks", json=request).json()["task_id"]
        second = self.client.post("/api/tasks", json=request).json()["task_id"]
        second_events = self.client.get(f"/api/tasks/{second}/events").json()["events"]
        self.assertIn("task_queued", {event["type"] for event in second_events})
        self.client.post(f"/api/tasks/{first}/cancel")
        self.client.post(f"/api/tasks/{second}/cancel")

    def test_hardware_resolve_keeps_unknowns_and_user_overrides(self) -> None:
        response = self.client.post(
            "/api/hardware/resolve",
            json={"gpu_model": "Metax C500", "backend": "mxmaca", "user_overrides": {"max_threads_per_block": 1024}},
        )
        self.assertEqual(response.status_code, 200)
        fields = response.json()["hardware"]["fields"]
        self.assertEqual(fields["backend"]["source"], "user_override")
        self.assertEqual(fields["max_threads_per_block"]["value"], 1024)
        self.assertIn("unknown_fields", response.json()["hardware"])


if __name__ == "__main__":
    unittest.main()
