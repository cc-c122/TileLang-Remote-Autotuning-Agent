from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kernel_opt_agent.api.server import create_server


ROOT = Path(__file__).resolve().parents[1]


def request_json(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    return resp.status, json.loads(raw) if raw else {}


def request_text(port: int, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    return resp.status, raw


def smoke_run_request() -> dict:
    return {
        "schema_version": "v2.run_request.v1",
        "project_name": "api-smoke",
        "sample": {
            "source_type": "path",
            "inline_text": None,
            "path": str(ROOT / "kernel_opt_agent" / "samples" / "tilelang_mock_minimal"),
            "entry_file": "kernel.py",
        },
        "commands": {
            "build_command": "python -m py_compile kernel.py",
            "correctness_command": "python correctness.py",
            "benchmark_command": "python benchmark.py",
        },
        "target": {"gpu_model": "Metax C500", "backend": "unknown"},
        "settings_ref": {"use_saved_settings": False},
        "hardware_overrides": {"fields": {}},
        "search": {
            "strategy": "rule_based",
            "max_iterations": 1,
            "candidates_per_iteration": 1,
            "timeout_seconds": 60,
            "objective": "latency",
        },
        "profiler": {"enabled": True, "type": "dummy"},
        "patching": {"enabled": True},
    }


class LocalApiTests(unittest.TestCase):
    def start_server(self, settings_path: Path):
        server = create_server("127.0.0.1", 0, settings_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_create_server_allows_loopback_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            server = create_server("127.0.0.1", 0, settings_path)
            server.server_close()
            server = create_server("localhost", 0, settings_path)
            server.server_close()
            server = create_server("LOCALHOST", 0, settings_path)
            server.server_close()

    def test_create_server_rejects_non_loopback_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.yaml"
            for host in ("0.0.0.0", "::", "192.168.1.10", "10.0.0.5", "172.16.0.2", "8.8.8.8", ""):
                with self.subTest(host=host):
                    with self.assertRaisesRegex(ValueError, "loopback"):
                        create_server(host, 0, settings_path)

    def test_local_api_smoke_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            port = self.start_server(Path(tmp) / "settings.yaml")
            status, payload = request_json(
                port,
                "POST",
                "/settings",
                {
                    "schema_version": "v2.user_settings.v1",
                    "runner": {"type": "ssh"},
                    "remote": {
                        "host": "example.invalid",
                        "port": 22,
                        "username": "root",
                        "auth_type": "password",
                        "password_env": "KERNEL_AGENT_SSH_PASSWORD",
                        "remote_workspace": "/tmp/kernel-agent",
                    },
                    "llm": {"api_key_env": "OPENAI_API_KEY"},
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            settings_text = Path(payload["settings_path"]).read_text(encoding="utf-8")
            self.assertIn("password_env: KERNEL_AGENT_SSH_PASSWORD", settings_text)
            self.assertIn("api_key_env: OPENAI_API_KEY", settings_text)

            status, payload = request_json(port, "POST", "/runs", smoke_run_request())
            self.assertEqual(status, 202)
            run_id = payload["run"]["run_id"]

            final_status = None
            for _ in range(60):
                status, payload = request_json(port, "GET", f"/runs/{run_id}/status")
                self.assertEqual(status, 200)
                final_status = payload["run"]["status"]
                if final_status in {"succeeded", "failed"}:
                    break
                time.sleep(0.2)
            self.assertEqual(final_status, "succeeded")

            status, payload = request_json(port, "GET", f"/runs/{run_id}/results")
            self.assertEqual(status, 200)
            self.assertTrue(payload["results"]["files"]["best_kernel.py"]["exists"])
            self.assertTrue(payload["results"]["files"]["report.md"]["exists"])

            status, best_kernel = request_text(port, f"/runs/{run_id}/best_kernel")
            self.assertEqual(status, 200)
            self.assertIn("BM", best_kernel)
            status, report = request_text(port, f"/runs/{run_id}/report")
            self.assertEqual(status, 200)
            self.assertIn("TileLang Autotuning Report", report)

    def test_settings_reject_plaintext_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            port = self.start_server(Path(tmp) / "settings.yaml")
            status, payload = request_json(
                port,
                "POST",
                "/settings",
                {"remote": {"password": "real-password"}, "llm": {"api_key": "sk-real-secret"}},
            )
            self.assertEqual(status, 400)
            self.assertFalse(payload["ok"])
            self.assertIn("sensitive value field is not allowed", payload["error"])


if __name__ == "__main__":
    unittest.main()
