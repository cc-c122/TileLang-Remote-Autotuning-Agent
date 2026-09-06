from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLAIN_KERNEL = """from helper import VALUE

def kernel_score():
    return VALUE
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_text(url: str) -> str:
    with urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8")


def _request_multipart(url: str, entry_file: str, files: list[tuple[str, bytes]]) -> dict:
    boundary = f"----tilelang-agent-{uuid.uuid4().hex}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="entry_file"\r\n\r\n')
    body.extend(entry_file.encode("utf-8"))
    body.extend(b"\r\n")
    for filename, content in files:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode()
        )
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    request = Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


class FastApiHttpSmokeTests(unittest.TestCase):
    def test_real_http_local_runner_smoke(self) -> None:
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        proc = subprocess.Popen(
            [sys.executable, "-m", "kernel_opt_agent.server.app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                try:
                    settings = _request_json("GET", f"{base}/api/settings")
                    if settings.get("ok"):
                        break
                except (HTTPError, URLError, TimeoutError, ConnectionError):
                    time.sleep(0.25)
            else:
                self.fail("FastAPI server did not become ready")

            health = _request_json("GET", f"{base}/api/health")
            self.assertEqual(health, {"ok": True, "service": "tilelang-agent", "mode": "local-runner"})
            self.assertIn("<title>TileLang", _request_text(f"{base}/"))

            saved = _request_json(
                "POST",
                f"{base}/api/settings",
                {
                    "ssh": {
                        "host": "example.invalid",
                        "port": 22,
                        "username": "user",
                        "auth_type": "key",
                        "key_path": "~/.ssh/id_rsa",
                        "password_env": "KERNEL_AGENT_SSH_PASSWORD",
                        "remote_workspace": "/tmp/kernel-agent",
                    },
                    "llm": {
                        "provider": "openai_compatible",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key_env": "OPENAI_API_KEY",
                    },
                },
            )
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["settings"]["ssh"]["key_path"], "<redacted:key_path>")
            self.assertNotIn("real-password", json.dumps(saved).lower())
            self.assertNotIn("api_key_value", json.dumps(saved).lower())
            self.assertNotIn("sk-", json.dumps(saved).lower())

            hardware = _request_json(
                "POST",
                f"{base}/api/hardware/resolve",
                {"gpu_model": "Metax C500", "backend": "mxmaca", "user_overrides": {"max_threads_per_block": 1024}},
            )
            self.assertTrue(hardware["ok"])
            self.assertEqual(hardware["hardware"]["fields"]["max_threads_per_block"]["source"], "user_override")

            uploaded = _request_multipart(
                f"{base}/api/samples/upload",
                "kernel.py",
                [
                    ("kernel.py", PLAIN_KERNEL.encode("utf-8")),
                    ("helper.py", b"VALUE = 7\n"),
                ],
            )
            self.assertTrue(uploaded["ok"])
            self.assertEqual(uploaded["entry_file"], "kernel.py")
            self.assertNotIn("sha256", json.dumps(uploaded).lower())

            created = _request_json(
                "POST",
                f"{base}/api/tasks",
                {
                    "project_name": "http-smoke-demo",
                    "sample": {
                        "source_type": "upload",
                        "upload_id": uploaded["upload_id"],
                        "entry_file": "kernel.py",
                    },
                    "target": {"gpu_model": "unknown", "backend": "unknown", "user_overrides": {}},
                    "commands": {
                        "build_command": "python -m py_compile kernel.py",
                        "correctness_command": "python -c \"import kernel; assert kernel.kernel_score() == 7; print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"",
                        "benchmark_command": "python -c \"import kernel; print(f'BENCHMARK_RESULT latency_ms={kernel.kernel_score()/7:.4f} tflops=1.0 bandwidth_gbps=2.0')\"",
                    },
                    "budget": {
                        "strategy": "rule_based",
                        "max_iterations": 1,
                        "candidates_per_iteration": 1,
                        "timeout_seconds": 30,
                        "objective": "latency",
                    },
                    "profiler": {"enabled": True, "type": "dummy"},
                    "patching": {"enabled": False, "run_controlled_trial": False},
                },
            )
            self.assertTrue(created["ok"])
            task_id = created["task_id"]

            final_task = None
            event_types: set[str] = set()
            for _ in range(120):
                task_payload = _request_json("GET", f"{base}/api/tasks/{task_id}")
                events_payload = _request_json("GET", f"{base}/api/tasks/{task_id}/events")
                final_task = task_payload["task"]
                event_types = {event["type"] for event in events_payload["events"]}
                if final_task["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.25)
            self.assertIsNotNone(final_task)
            self.assertEqual(final_task["status"], "completed")
            self.assertEqual(final_task["execution_mode"], "baseline_only")
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
            ordered_events = [event["type"] for event in events_payload["events"]]
            self.assertLess(ordered_events.index("correctness_started"), ordered_events.index("benchmark_started"))

            workspace = Path(final_task["workspace"])
            self.assertTrue((workspace / "run_request.yaml").exists())
            effective_config = workspace / "effective_config.yaml"
            self.assertTrue(effective_config.exists())
            effective_text = effective_config.read_text(encoding="utf-8").lower()
            self.assertNotIn("password:", effective_text)
            self.assertNotIn("api_key:", effective_text)
            self.assertNotIn("token:", effective_text)

            results = _request_json("GET", f"{base}/api/tasks/{task_id}/results")
            self.assertTrue(results["ok"])
            result_payload = results["results"]
            self.assertEqual(results["task"]["project_name"], "http-smoke-demo")
            self.assertIn("def kernel_score", result_payload["best_kernel"])
            self.assertEqual(result_payload["best_config"], {})
            self.assertIn("TileLang Autotuning Report", result_payload["report_markdown"])
            self.assertIn("baseline-only", result_payload["report_markdown"])
            self.assertIsInstance(result_payload["summary_table"], list)
            self.assertIsInstance(result_payload["failed_cases"], list)
            self.assertIsNone(result_payload["improvement_percent"])
            self.assertTrue(result_payload["generated_files"]["best_kernel.py"]["exists"])

            best_kernel = _request_text(f"{base}/api/tasks/{task_id}/download/best_kernel")
            report = _request_text(f"{base}/api/tasks/{task_id}/download/report")
            self.assertIn("def kernel_score", best_kernel)
            self.assertIn("TileLang Autotuning Report", report)
            task_list = _request_json("GET", f"{base}/api/tasks")
            listed = next(item for item in task_list["tasks"] if item["task_id"] == task_id)
            self.assertEqual(listed["project_name"], "http-smoke-demo")
            self.assertEqual(listed["status"], "completed")
            self.assertIn("latest_message", listed)
            self.assertIn("improvement_percent", listed)
            self.assertNotIn("real-password", json.dumps(listed).lower())
            self.assertNotIn("sk-", json.dumps(listed).lower())
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
