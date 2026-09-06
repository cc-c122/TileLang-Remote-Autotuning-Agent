from __future__ import annotations

import asyncio
import csv
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from fastapi.testclient import TestClient

import kernel_opt_agent.sample_security as sample_security
from kernel_opt_agent.config_model import AppConfig, load_config
from kernel_opt_agent.kernel.variant_generator import VariantGenerator
from kernel_opt_agent.server.app import TASKS_ROOT, app, upload_store
from kernel_opt_agent.server.models import TaskCreateRequest
from kernel_opt_agent.server.run_request_builder import materialize_sample
from kernel_opt_agent.server.sample_uploads import (
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    SampleUploadError,
    SampleUploadStore,
    _publish_sample_directory,
)


ROOT = Path(__file__).resolve().parents[1]
KERNEL_SOURCE = b"""from pathlib import Path
from helper import VALUE

def kernel_score():
    return VALUE + int(Path('assets/value.txt').read_text(encoding='utf-8'))
"""
HELPER_SOURCE = b"VALUE = 4\nRAW_TEMPLATE_TEXT = '{{DEPENDENCY_LITERAL}}'\n"


def _multipart_body(boundary: str, filename: str, content: bytes, entry_file: str = "kernel.py") -> bytes:
    return b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="entry_file"\r\n\r\n',
            entry_file.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )


async def _stream_request(body: bytes, boundary: str, content_length: bytes | None) -> tuple[int, int]:
    offset = 0
    received = 0
    status = 0
    headers = [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]
    if content_length is not None:
        headers.append((b"content-length", content_length))

    async def receive() -> dict:
        nonlocal offset, received
        chunk = body[offset : offset + 64 * 1024]
        offset += len(chunk)
        received += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": offset < len(body)}

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/samples/upload",
        "raw_path": b"/api/samples/upload",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    await app(scope, receive, send)
    return status, received


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

    def test_directory_publication_retries_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "stage", Path(tmp) / "sample"
            source.mkdir()
            original_rename = Path.rename
            attempts = []

            def locked_once(path, target):
                attempts.append((path, target))
                if len(attempts) == 1:
                    error = PermissionError("transient file lock")
                    error.winerror = 32
                    raise error
                return original_rename(path, target)

            with patch.object(Path, "rename", locked_once), patch("kernel_opt_agent.server.sample_uploads.time.sleep") as sleep:
                _publish_sample_directory(source, destination)
            self.assertEqual(len(attempts), 2)
            sleep.assert_called_once_with(0.05)
            self.assertTrue(destination.is_dir())
            self.assertFalse(source.exists())

    def test_directory_publication_does_not_hide_persistent_or_other_errors(self) -> None:
        for windows_error, expected_attempts in ((5, 5), (None, 1)):
            with self.subTest(windows_error=windows_error), tempfile.TemporaryDirectory() as tmp:
                source, destination = Path(tmp) / "stage", Path(tmp) / "sample"
                source.mkdir()
                error = PermissionError("persistent access denied")
                if windows_error is not None:
                    error.winerror = windows_error
                with patch.object(Path, "rename", side_effect=error) as rename, patch("kernel_opt_agent.server.sample_uploads.time.sleep") as sleep:
                    with self.assertRaises(PermissionError):
                        _publish_sample_directory(source, destination)
                self.assertEqual(rename.call_count, expected_attempts)
                self.assertEqual(sleep.call_count, expected_attempts - 1)
                self.assertTrue(source.exists())
                self.assertFalse(destination.exists())

    def test_directory_publication_never_replaces_an_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / "stage", Path(tmp) / "sample"
            source.mkdir()
            destination.mkdir()
            with patch.object(Path, "rename") as rename:
                with self.assertRaisesRegex(SampleUploadError, "already exists"):
                    _publish_sample_directory(source, destination)
            rename.assert_not_called()
            self.assertTrue(source.is_dir())
            self.assertTrue(destination.is_dir())

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
            "bad<name.py",
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

    def test_streaming_upload_stops_before_reading_oversized_body_without_content_length(self) -> None:
        boundary = "tilelang-agent-stream-boundary"
        body = _multipart_body(boundary, "kernel.py", b"x" * (12 * 1024 * 1024))
        parser_files = []

        def tracked_spooled_file(*args, **kwargs):
            file = tempfile.SpooledTemporaryFile(*args, **kwargs)
            parser_files.append(file)
            return file

        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=tracked_spooled_file):
            for content_length in (None, b"1", b"not-a-number"):
                with self.subTest(content_length=content_length):
                    status, received = asyncio.run(_stream_request(body, boundary, content_length))
                    self.assertEqual(status, 413)
                    self.assertLess(received, len(body))
                    self.assertLessEqual(received, MAX_UPLOAD_FILE_BYTES + 128 * 1024)
            status, received = asyncio.run(_stream_request(body, boundary, str(len(body)).encode()))
            self.assertEqual(status, 413)
            self.assertEqual(received, 0)
        self.assertTrue(parser_files)
        self.assertTrue(all(file.closed for file in parser_files))
        self.assertFalse(any(path.name.startswith(".staging-") for path in upload_store.root.iterdir()))

    def test_upload_rejects_file_directory_prefix_conflicts_in_both_orders(self) -> None:
        for files in (
            [("a", b"file"), ("a/kernel.py", b"print('x')")],
            [("a/kernel.py", b"print('x')"), ("a", b"file")],
        ):
            with self.subTest(files=[name for name, _ in files]):
                response = self._upload(files, entry_file="a/kernel.py")
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("conflict", response.text.lower())
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

        metadata_too_large = self._upload([("kernel.py", b"x")], entry_file="x" * 5000)
        self.assertEqual(metadata_too_large.status_code, 413)

        too_large = self._upload([("kernel.py", b"x" * (MAX_UPLOAD_FILE_BYTES + 1))])
        self.assertEqual(too_large.status_code, 413)

        part_size = MAX_UPLOAD_TOTAL_BYTES // 6 + 1
        total_too_large = self._upload(
            [("kernel.py", b"x" * part_size)]
            + [(f"part_{index}.txt", b"x" * part_size) for index in range(1, 6)]
        )
        self.assertEqual(total_too_large.status_code, 413)

    def test_unexpected_upload_field_closes_every_parsed_temporary_file(self) -> None:
        parser_files = []

        def tracked_spooled_file(*args, **kwargs):
            file = tempfile.SpooledTemporaryFile(*args, **kwargs)
            parser_files.append(file)
            return file

        with patch("starlette.formparsers.SpooledTemporaryFile", side_effect=tracked_spooled_file):
            response = self.client.post(
                "/api/samples/upload",
                data={"entry_file": "kernel.py"},
                files=[
                    ("evil", ("ignored.txt", b"ignored", "application/octet-stream")),
                    ("files", ("kernel.py", b"print('ok')", "application/octet-stream")),
                ],
            )
        self.assertEqual(response.status_code, 422)
        self.assertTrue(parser_files)
        self.assertTrue(all(file.closed for file in parser_files))

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

    def test_upload_materialization_rejects_replaced_upload_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SampleUploadStore(Path(tmp) / "uploads")
            stream = tempfile.SpooledTemporaryFile()
            stream.write(b"print('ok')\n")
            stream.seek(0)
            upload = UploadFile(file=stream, filename="kernel.py")
            try:
                manifest = asyncio.run(store.create([upload], "kernel.py"))
            finally:
                asyncio.run(upload.close())
            upload_root = store.root / manifest["upload_id"]
            real_check = sample_security.is_link_or_reparse

            def fake_link_check(path: Path) -> bool:
                return path.absolute() == upload_root.absolute() or real_check(path)

            with patch.object(sample_security, "is_link_or_reparse", side_effect=fake_link_check):
                with self.assertRaisesRegex(SampleUploadError, "link or reparse"):
                    store.materialize(manifest["upload_id"], "kernel.py", Path(tmp) / "task" / "sample")

    def test_inline_and_path_materialization_share_sensitive_sample_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inline_raw = _task_request("0" * 32)
            inline_raw["sample"] = {
                "source_type": "inline",
                "inline_text": "secret",
                "entry_file": ".env",
            }
            inline = TaskCreateRequest.model_validate(inline_raw)
            with self.assertRaisesRegex(ValueError, "sensitive"):
                materialize_sample(inline, root / "inline-task")

            private_raw = _task_request("0" * 32)
            private_raw["sample"] = {
                "source_type": "inline",
                "inline_text": "-----BEGIN PRIVATE KEY-----\nsecret\n",
                "entry_file": "kernel.py",
            }
            private_inline = TaskCreateRequest.model_validate(private_raw)
            with self.assertRaisesRegex(ValueError, "private key"):
                materialize_sample(private_inline, root / "private-inline-task")

            valid_source = root / "valid-source"
            (valid_source / "assets").mkdir(parents=True)
            (valid_source / "kernel.py").write_text("VALUE = 1\n", encoding="utf-8")
            (valid_source / "assets" / "weights.data").write_bytes(b"data")
            path_raw = _task_request("0" * 32)
            path_raw["sample"] = {
                "source_type": "path",
                "path": str(valid_source),
                "entry_file": "kernel.py",
            }
            path_request = TaskCreateRequest.model_validate(path_raw)
            materialized = materialize_sample(path_request, root / "valid-path-task")
            self.assertEqual((materialized / "assets" / "weights.data").read_bytes(), b"data")

            sensitive_source = root / "sensitive-source"
            sensitive_source.mkdir()
            (sensitive_source / "kernel.py").write_text("VALUE = 1\n", encoding="utf-8")
            (sensitive_source / ".env").write_text("PASSWORD=secret\n", encoding="utf-8")
            path_raw["sample"]["path"] = str(sensitive_source)
            sensitive_request = TaskCreateRequest.model_validate(path_raw)
            with self.assertRaisesRegex(ValueError, "sensitive"):
                materialize_sample(sensitive_request, root / "sensitive-path-task")

            key_source = root / "key-source"
            key_source.mkdir()
            (key_source / "kernel.py").write_text("VALUE = 1\n", encoding="utf-8")
            (key_source / "notes.txt").write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n",
                encoding="utf-8",
            )
            path_raw["sample"]["path"] = str(key_source)
            key_request = TaskCreateRequest.model_validate(path_raw)
            with self.assertRaisesRegex(ValueError, "private key"):
                materialize_sample(key_request, root / "key-path-task")

    def test_path_and_variant_copy_reject_link_or_reparse_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "kernel.py").write_text("VALUE = 1\n", encoding="utf-8")
            raw = _task_request("0" * 32)
            raw["sample"] = {
                "source_type": "path",
                "path": str(source),
                "entry_file": "kernel.py",
            }
            request = TaskCreateRequest.model_validate(raw)
            real_check = sample_security.is_link_or_reparse

            def fake_link_check(path: Path) -> bool:
                return path.absolute() == source.absolute() or real_check(path)

            with patch.object(sample_security, "is_link_or_reparse", side_effect=fake_link_check):
                with self.assertRaisesRegex(ValueError, "link or reparse"):
                    materialize_sample(request, root / "task")
                generator = VariantGenerator(
                    source,
                    "kernel.py",
                    {},
                    root / "linked-generated",
                    root / "linked-patches",
                    ["*.py"],
                )
                with self.assertRaisesRegex(ValueError, "link or reparse"):
                    generator.create_trial(0, 0, {})

            (source / ".env").write_text("PASSWORD=secret\n", encoding="utf-8")
            generator = VariantGenerator(
                source,
                "kernel.py",
                {},
                root / "generated",
                root / "patches",
                ["*.py"],
            )
            with self.assertRaisesRegex(ValueError, "sensitive"):
                generator.create_trial(0, 0, {})
            self.assertFalse((root / "generated" / "iter000_cand000" / ".env").exists())

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
        self.assertIn("build_started", event_types)
        self.assertIn("correctness_started", event_types)
        self.assertLess(event_types.index("build_started"), event_types.index("correctness_started"))
        self.assertNotIn("benchmark_started", event_types)
        self.assertNotIn("best_updated", event_types)
        results_dir = Path(task["results_dir"])
        with (results_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "correctness_failed")
        logs = "\n".join(path.read_text(encoding="utf-8") for path in (results_dir / "logs").glob("*.log"))
        self.assertNotIn("SHOULD_NOT_RUN", logs)

    def test_build_artifact_is_available_to_correctness_and_build_failure_stops_trial(self) -> None:
        upload_id = self._valid_upload()
        success = _task_request(upload_id, project_name="build-before-correctness")
        success["commands"]["build_command"] = (
            "python -c \"from pathlib import Path; Path('built.txt').write_text('ready')\""
        )
        success["commands"]["correctness_command"] = (
            "python -c \"from pathlib import Path; assert Path('built.txt').read_text() == 'ready'; "
            "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\""
        )
        created = self.client.post("/api/tasks", json=success)
        task = self._wait(created.json()["task_id"])
        self.assertEqual(task["status"], "completed")
        event_types = [
            event["type"]
            for event in self.client.get(f"/api/tasks/{task['task_id']}/events").json()["events"]
        ]
        self.assertLess(event_types.index("build_started"), event_types.index("correctness_started"))
        self.assertLess(event_types.index("correctness_started"), event_types.index("benchmark_started"))
        self.assertIn("best_updated", event_types)

        failed = _task_request(upload_id, project_name="build-failure-gate")
        failed["commands"]["build_command"] = "python -c \"import sys; print('BUILD_FAILED'); sys.exit(1)\""
        failed["commands"]["correctness_command"] = (
            "python -c \"print('SHOULD_NOT_RUN_CORRECTNESS'); "
            "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\""
        )
        failed["commands"]["benchmark_command"] = (
            "python -c \"print('SHOULD_NOT_RUN_BENCHMARK'); "
            "print('BENCHMARK_RESULT latency_ms=1 tflops=1 bandwidth_gbps=1')\""
        )
        failed_task = self._wait(self.client.post("/api/tasks", json=failed).json()["task_id"])
        failed_events = [
            event["type"]
            for event in self.client.get(f"/api/tasks/{failed_task['task_id']}/events").json()["events"]
        ]
        self.assertIn("build_started", failed_events)
        self.assertNotIn("correctness_started", failed_events)
        self.assertNotIn("benchmark_started", failed_events)
        self.assertNotIn("best_updated", failed_events)
        with (Path(failed_task["results_dir"]) / "summary.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "build_failed")
        logs = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (Path(failed_task["results_dir"]) / "logs").glob("*.log")
        )
        self.assertNotIn("SHOULD_NOT_RUN_CORRECTNESS", logs)
        self.assertNotIn("SHOULD_NOT_RUN_BENCHMARK", logs)

    def test_baseline_only_never_executes_legacy_controlled_patch(self) -> None:
        source = b"""def kernel_score():
    # BEGIN_AGENT_PATCH: compute
    value = 7
    # END_AGENT_PATCH
    return value
"""
        upload = self._upload([("kernel.py", source)])
        request = _task_request(upload.json()["upload_id"], project_name="baseline-no-patch")
        request["commands"] = {
            "build_command": "python -m py_compile kernel.py",
            "correctness_command": (
                "python -c \"import kernel; assert kernel.kernel_score() == 7; "
                "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\""
            ),
            "benchmark_command": (
                "python -c \"print('BENCHMARK_RESULT latency_ms=1 tflops=1 bandwidth_gbps=1')\""
            ),
        }
        request["patching"] = {"enabled": True, "run_controlled_trial": True}
        task = self._wait(self.client.post("/api/tasks", json=request).json()["task_id"])
        self.assertEqual(task["execution_mode"], "baseline_only")
        patch_trials = (Path(task["results_dir"]) / "patch_trials.jsonl").read_text(encoding="utf-8")
        self.assertEqual(patch_trials, "")
        report = (Path(task["results_dir"]) / "report.md").read_text(encoding="utf-8")
        self.assertIn("No source optimization or parameter search was performed", report)
        best_kernel = (Path(task["results_dir"]) / "best_kernel.py").read_text(encoding="utf-8")
        self.assertNotIn("controlled_patch_trial", best_kernel)

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
