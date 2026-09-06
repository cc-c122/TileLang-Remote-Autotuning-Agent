from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import UploadFile
from fastapi.testclient import TestClient

from kernel_opt_agent.config_model import AppConfig, load_config
from kernel_opt_agent.server.app import TASKS_ROOT, app, upload_store
from kernel_opt_agent.server.sample_uploads import (
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    SampleUploadError,
    SampleUploadStore,
)


ROOT = Path(__file__).resolve().parents[1]
KERNEL_SOURCE = b"""from pathlib import Path
from helper import VALUE

def kernel_score():
    return VALUE + int(Path('assets/value.txt').read_text(encoding='utf-8'))
"""
HELPER_SOURCE = b"VALUE = 4\nRAW_TEMPLATE_TEXT = '{{DEPENDENCY_LITERAL}}'\n"


def _task_request(upload_id: str, *, project_name: str = "uploaded-baseline") -> dict:
    return {
        "project_name": project_name,
        "runner": {"type": "local"},
        "sample": {
            "source_type": "upload",
            "upload_id": upload_id,
            "entry_file": "kernel.py",
        },
        "target": {"gpu_model": "unknown", "backend": "unknown", "user_overrides": {}},
        "commands": {
            "build_command": "python -m py_compile kernel.py helper.py",
            "correctness_command": (
                "python -c \"import kernel; assert kernel.kernel_score() == 7; "
                "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\""
            ),
            "benchmark_command": (
                "python -c \"import kernel; print(f'BENCHMARK_RESULT latency_ms={kernel.kernel_score()/7:.3f} "
                "tflops=1.0 bandwidth_gbps=2.0')\""
            ),
        },
        "budget": {
            "strategy": "rule_based",
            "max_iterations": 3,
            "candidates_per_iteration": 3,
            "timeout_seconds": 10,
            "objective": "latency",
        },
        "profiler": {"enabled": False, "type": "dummy"},
        "patching": {"enabled": False, "run_controlled_trial": False},
    }


class WebSampleRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _upload(self, files: list[tuple[str, bytes]], entry_file: str = "kernel.py"):
        multipart = [
            ("files", (name, content, "application/octet-stream"))
            for name, content in files
        ]
        return self.client.post(
            "/api/samples/upload",
            data={"entry_file": entry_file},
            files=multipart,
        )

    def _valid_upload(self) -> str:
        response = self._upload(
            [
                ("kernel.py", KERNEL_SOURCE),
                ("helper.py", HELPER_SOURCE),
                ("assets/value.txt", b"3"),
            ]
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["upload_id"]

    def _wait(self, task_id: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        task = {}
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(response.status_code, 200)
            task = response.json()["task"]
            if task["status"] in {"completed", "failed", "cancelled"}:
                return task
            time.sleep(0.05)
        self.fail(f"task did not finish: {task}")

    def test_root_serves_only_frontend_and_unknown_api_stays_api_404(self) -> None:
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("<title>TileLang", root.text)
        self.assertEqual(self.client.get("/README.md").status_code, 404)
        missing_api = self.client.get("/api/not-a-real-route")
        self.assertEqual(missing_api.status_code, 404)
        self.assertEqual(missing_api.json(), {"detail": "API route not found"})

    def test_upload_returns_public_manifest_shape_without_server_paths(self) -> None:
        response = self._upload([("kernel.py", KERNEL_SOURCE), ("helper.py", HELPER_SOURCE)])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(set(body), {"ok", "upload_id", "entry_file", "files"})
        self.assertEqual(body["entry_file"], "kernel.py")
        self.assertEqual(
            body["files"],
            [
                {"path": "kernel.py", "size_bytes": len(KERNEL_SOURCE)},
                {"path": "helper.py", "size_bytes": len(HELPER_SOURCE)},
            ],
        )
        serialized = json.dumps(body)
        self.assertNotIn(str(upload_store.root), serialized)
        self.assertNotIn("sha256", serialized)
        manifest = upload_store.load_manifest(body["upload_id"])
        self.assertEqual(len(manifest["files"][0]["sha256"]), 64)

    def test_upload_rejects_unsafe_and_sensitive_paths(self) -> None:
        bad_paths = [
            "../kernel.py",
            "/kernel.py",
            "C:/kernel.py",
            "folder\\kernel.py",
            ".git/config",
            ".ssh/config",
            ".env",
            ".env.local/secrets.txt",
            "keys/deploy.pem",
        ]
        for filename in bad_paths:
            with self.subTest(filename=filename):
                response = self._upload([(filename, b"print('x')\n")], entry_file=filename)
                self.assertEqual(response.status_code, 422, response.text)
        private_key = self._upload(
            [("notes.txt", b"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n")],
            entry_file="notes.txt",
        )
        self.assertEqual(private_key.status_code, 422)
        private_key_after_large_prefix = self._upload(
            [("notes.txt", b"x" * 5000 + b"-----BEGIN PRIVATE KEY-----\nsecret\n")],
            entry_file="notes.txt",
        )
        self.assertEqual(private_key_after_large_prefix.status_code, 422)
        self.assertFalse(any(path.name.startswith(".staging-") for path in upload_store.root.iterdir()))

    def test_upload_rejects_duplicates_missing_entry_and_limits(self) -> None:
        duplicate = self._upload([("Kernel.py", b"a"), ("kernel.py", b"b")])
        self.assertEqual(duplicate.status_code, 422)
        self.assertIn("duplicate", duplicate.text.lower())

        missing_entry = self._upload([("helper.py", b"VALUE = 1\n")])
        self.assertEqual(missing_entry.status_code, 422)
        self.assertIn("entry_file", missing_entry.text)

        too_many = self._upload(
            [(f"part_{index}.txt", b"x") for index in range(MAX_UPLOAD_FILES + 1)],
            entry_file="part_0.txt",
        )
        self.assertEqual(too_many.status_code, 413)

        too_large = self._upload([("kernel.py", b"x" * (MAX_UPLOAD_FILE_BYTES + 1))])
        self.assertEqual(too_large.status_code, 413)

        part_size = MAX_UPLOAD_TOTAL_BYTES // 6 + 1
        total_too_large = self._upload(
            [("kernel.py", b"x" * part_size)]
            + [(f"part_{index}.txt", b"x" * part_size) for index in range(1, 6)]
        )
        self.assertEqual(total_too_large.status_code, 413)

    def test_upload_store_rejects_non_regular_file_streams(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with tempfile.TemporaryDirectory() as tmp, os.fdopen(read_fd, "rb") as stream:
                upload = UploadFile(file=stream, filename="kernel.py")
                store = SampleUploadStore(Path(tmp) / "uploads")
                with self.assertRaisesRegex(SampleUploadError, "regular files"):
                    asyncio.run(store.create([upload], "kernel.py"))
        finally:
            os.close(write_fd)

    def test_unknown_upload_is_rejected_before_task_workspace_creation(self) -> None:
        before = {path.name for path in TASKS_ROOT.iterdir()} if TASKS_ROOT.exists() else set()
        request = _task_request("0" * 32)
        response = self.client.post("/api/tasks", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertIn("unknown sample.upload_id", response.text)
        after = {path.name for path in TASKS_ROOT.iterdir()} if TASKS_ROOT.exists() else set()
        self.assertEqual(after, before)

    def test_upload_task_cannot_substitute_a_server_path_for_upload_id(self) -> None:
        request = _task_request("0" * 32)
        request["sample"]["path"] = str(ROOT)
        response = self.client.post("/api/tasks", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertIn("sample.inline_text and sample.path must be omitted", response.text)

    def test_uploaded_plain_source_runs_once_and_two_tasks_are_isolated(self) -> None:
        upload_id = self._valid_upload()
        first_response = self.client.post("/api/tasks", json=_task_request(upload_id, project_name="baseline-one"))
        second_response = self.client.post("/api/tasks", json=_task_request(upload_id, project_name="baseline-two"))
        self.assertEqual(first_response.status_code, 200, first_response.text)
        self.assertEqual(second_response.status_code, 200, second_response.text)
        first = self._wait(first_response.json()["task_id"])
        second = self._wait(second_response.json()["task_id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(first["execution_mode"], "baseline_only")
        self.assertEqual(second["execution_mode"], "baseline_only")

        first_workspace = Path(first["workspace"])
        second_workspace = Path(second["workspace"])
        for workspace in (first_workspace, second_workspace):
            self.assertIn(
                "execution_mode: baseline_only",
                (workspace / "effective_config.yaml").read_text(encoding="utf-8"),
            )
            task_manifest = json.loads((workspace / "sample_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(task_manifest["upload_id"], upload_id)
            self.assertEqual(len(task_manifest["files"][0]["sha256"]), 64)
            self.assertTrue((workspace / "sample" / "assets" / "value.txt").is_file())
            generated_helper = workspace / "generated" / "iter000_cand000" / "helper.py"
            self.assertIn("{{DEPENDENCY_LITERAL}}", generated_helper.read_text(encoding="utf-8"))
            self.assertTrue((workspace / "generated" / "iter000_cand000" / "assets" / "value.txt").is_file())
            with (workspace / "results" / "summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "benchmark_ok")
            self.assertIn("running baseline", (workspace / "results" / "agent.log").read_text(encoding="utf-8"))

        (first_workspace / "sample" / "helper.py").write_text("VALUE = 999\n", encoding="utf-8")
        self.assertIn("VALUE = 4", (second_workspace / "sample" / "helper.py").read_text(encoding="utf-8"))
        upload_root = upload_store.root / upload_id / "sample"
        self.assertIn("VALUE = 4", (upload_root / "helper.py").read_text(encoding="utf-8"))

        result_response = self.client.get(f"/api/tasks/{first['task_id']}/results")
        result = result_response.json()["results"]
        self.assertIsNone(result["improvement_percent"])
        self.assertEqual(result["best_config"], {})
        self.assertIn("def kernel_score", result["best_kernel"])
        self.assertIn("baseline-only", result["report_markdown"])
        self.assertIn("No source optimization or parameter search was performed", result["report_markdown"])

    def test_correctness_failure_never_starts_benchmark(self) -> None:
        upload_id = self._valid_upload()
        request = _task_request(upload_id, project_name="correctness-gate")
        request["commands"]["correctness_command"] = (
            "python -c \"print('CORRECTNESS_RESULT status=FAIL max_error=1 reason=mismatch')\""
        )
        request["commands"]["benchmark_command"] = (
            "python -c \"print('SHOULD_NOT_RUN'); "
            "print('BENCHMARK_RESULT latency_ms=0.1 tflops=9 bandwidth_gbps=9')\""
        )
        created = self.client.post("/api/tasks", json=request)
        self.assertEqual(created.status_code, 200, created.text)
        task = self._wait(created.json()["task_id"])
        self.assertEqual(task["status"], "completed")
        events = self.client.get(f"/api/tasks/{task['task_id']}/events").json()["events"]
        event_types = [event["type"] for event in events]
        self.assertIn("correctness_started", event_types)
        self.assertNotIn("build_started", event_types)
        self.assertNotIn("benchmark_started", event_types)
        results_dir = Path(task["results_dir"])
        with (results_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "correctness_failed")
        logs = "\n".join(path.read_text(encoding="utf-8") for path in (results_dir / "logs").glob("*.log"))
        self.assertNotIn("SHOULD_NOT_RUN", logs)

    def test_timed_out_task_does_not_block_next_uploaded_task(self) -> None:
        upload_id = self._valid_upload()
        timed_out = _task_request(upload_id, project_name="timeout-first")
        timed_out["budget"]["timeout_seconds"] = 1
        timed_out["commands"]["benchmark_command"] = (
            "python -c \"import time; time.sleep(2); "
            "print('BENCHMARK_RESULT latency_ms=1 tflops=1 bandwidth_gbps=1')\""
        )
        first_id = self.client.post("/api/tasks", json=timed_out).json()["task_id"]
        second_id = self.client.post(
            "/api/tasks",
            json=_task_request(upload_id, project_name="after-timeout"),
        ).json()["task_id"]
        first = self._wait(first_id)
        second = self._wait(second_id)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        with (Path(first["results_dir"]) / "summary.csv").open(newline="", encoding="utf-8") as handle:
            first_rows = list(csv.DictReader(handle))
        self.assertEqual(first_rows[0]["status"], "run_timeout")
        self.assertIsNotNone(second["best_latency"])

    def test_baseline_mode_is_explicit_and_v1_empty_space_remains_invalid(self) -> None:
        source = ROOT / "kernel_opt_agent" / "samples" / "mock"
        raw = {
            "project_name": "explicit-baseline",
            "execution_mode": "baseline_only",
            "runner": {"type": "local"},
            "remote": {
                "auth_type": "password",
                "password_env": "KERNEL_AGENT_SSH_PASSWORD",
            },
            "kernel": {
                "sample_path": str(source),
                "entry_file": "correctness.py",
                "build_command": None,
                "correctness_command": "python correctness.py",
                "run_command": "python benchmark.py",
            },
            "search_space": {},
        }
        config = AppConfig.model_validate(raw)
        self.assertEqual(config.execution_mode, "baseline_only")
        raw.pop("execution_mode")
        with self.assertRaisesRegex(ValueError, "search_space is required"):
            AppConfig.model_validate(raw)
        v1 = load_config(str(ROOT / "kernel_opt_agent" / "config.example.yaml"))
        self.assertEqual(v1.execution_mode, "parameter_search")

    def test_local_task_artifacts_do_not_contain_environment_secret_values(self) -> None:
        secret = "web-upload-secret-value-never-persist"
        old = os.environ.get("KERNEL_AGENT_SSH_PASSWORD")
        os.environ["KERNEL_AGENT_SSH_PASSWORD"] = secret
        try:
            upload_id = self._valid_upload()
            created = self.client.post("/api/tasks", json=_task_request(upload_id, project_name="secret-scan"))
            task = self._wait(created.json()["task_id"])
            self.assertEqual(task["status"], "completed")
            for path in Path(task["workspace"]).rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                self.assertNotIn(secret, text, str(path))
            response_text = self.client.get(f"/api/tasks/{task['task_id']}/results").text
            self.assertNotIn(secret, response_text)
        finally:
            if old is None:
                os.environ.pop("KERNEL_AGENT_SSH_PASSWORD", None)
            else:
                os.environ["KERNEL_AGENT_SSH_PASSWORD"] = old


if __name__ == "__main__":
    unittest.main()
