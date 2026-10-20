from __future__ import annotations

import copy
import json
import logging
import hashlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import paramiko
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kernel_opt_agent.hardware.remote_gpu_detector import GPU_QUERY_COMMAND, query_remote_devices, resolve_remote_hardware
from kernel_opt_agent.hardware.remote_gpu_query import collect as collect_gpu_query
from kernel_opt_agent.runner.command_guard import validate
from kernel_opt_agent.runner.host_keys import HostKeyError, HostKeyStore
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.runner.ssh_runner import SSHRunner, SSHConnectionInfo
from kernel_opt_agent.server import app as server
from kernel_opt_agent.server.job_worker import JobWorker
from kernel_opt_agent.server.models import SSHSessionRequest
from kernel_opt_agent.server.security import validate_loopback_host
from kernel_opt_agent.server.ssh_sessions import SSHSessionStore, SessionError
from kernel_opt_agent.server.task_manager import TaskManager


def device(index=0, uuid=None):
    uuid = uuid or f"GPU-test-{index}"
    return {"device_id": "mxmaca:" + uuid, "backend": "mxmaca", "selector": uuid, "runtime_index": index,
            "identity_verified": True, "warnings": ["cu_count: unavailable from runtime"], "fields": {
                "device_id": "mxmaca:" + uuid, "device_uuid": uuid, "pci_bus_id": None, "target_name": "MetaX C500",
                "backend": "mxmaca", "total_memory_bytes": 64 * 1024**3, "wave_size": 64,
                "sm_count": None, "cu_count": None, "max_threads_per_block": 1024, "shared_memory_per_block_bytes": 65536}}


class FakeRunner:
    instances = []

    def __init__(self, info, timeout_seconds, *, password=None, host_key_store=None):
        self.info, self.timeout_seconds, self._password = info, timeout_seconds, password
        self.execution_env = {}
        self.devices = [device()]
        self.closed = False
        self.connect_count = 0
        self.commands = []
        self.upload_count = 0
        self.python_bin_dir = None
        self.instances.append(self)

    def redact(self, value):
        return value.replace(self._password.get_secret_value(), "<redacted:secret>") if self._password else value

    def connect(self):
        self.connect_count += 1
        value = self._password.get_secret_value() if self._password else ""
        if value == "wrong-password":
            raise paramiko.AuthenticationException(value)
        if value == "timeout-password":
            raise socket.timeout(value)

    def close(self):
        self.closed = True
        self._password = None

    def _mkdir_p(self, path):
        pass

    def _ensure_workspace_marker(self, path):
        pass

    def run_detection(self, name, command):
        return self._query(name, command, self.devices)

    def _query(self, name, command, devices):
        payload = {"devices": devices, "environment": {"python_version": "3.10", "device_count": len(devices)}, "warnings": []}
        return CommandResult(name, command, 0, "KERNEL_AGENT_GPU_QUERY=" + json.dumps(payload), "", 0, 1, 1)

    def run(self, name, command):
        self.commands.append((name, dict(self.execution_env), self.info.remote_workspace))
        if name == "hardware_gpu_query":
            selected = next(iter(self.execution_env.values()), None)
            devices = [item for item in self.devices if item["selector"] == selected] if selected else self.devices
            return self._query(name, command, devices)
        stdout = 'CORRECTNESS_RESULT status=PASS max_error=0 reason="ok"' if name == "correctness" else "BENCHMARK_RESULT latency_ms=1.25" if name.startswith("benchmark") else ""
        return CommandResult(name, command, 0, stdout, "", 0, 1, 1)

    def upload(self, path):
        self.upload_count += 1
        self.upload_dir = Path(path)

    def file_sha256(self, relative_path):
        return hashlib.sha256((self.upload_dir / relative_path).read_bytes()).hexdigest()

    def download_artifacts(self, path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "download.txt").write_text("remote artifact", encoding="utf-8")


class SSHSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = [1000.0]
        self.store = SSHSessionStore(HostKeyStore(self.root / "hosts.json"), clock=lambda: self.now[0], runner_factory=FakeRunner, evidence_root=self.root / "hardware")
        self.patch_store = patch.object(server, "ssh_sessions", self.store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.client = TestClient(server.app)
        self.addCleanup(lambda: [self.store.delete(key) for key in list(self.store._sessions)])

    def test_transport_thread_logs_are_redacted_without_task_context(self):
        from kernel_opt_agent.redaction import SessionLogFilter

        session_id = self.create("transport-thread-password").json()["session_id"]
        record = logging.LogRecord("paramiko", logging.WARNING, "", 1, "%s %s", ("transport-thread-password", session_id), None)
        SessionLogFilter().filter(record)
        self.assertNotIn("transport-thread-password", record.getMessage())
        self.assertNotIn(session_id, record.getMessage())

    def create(self, password="session-password-sentinel"):
        payload = {"ssh": {"host": "test.invalid", "username": "user", "auth_type": "password", "remote_workspace": "/tmp/managed-test"}, "password": password}
        return self.client.post("/api/ssh/sessions", json=payload)

    def test_wrong_password_and_connection_timeout_are_safe(self):
        for password, expected in (("wrong-password", "auth_failed"), ("timeout-password", "connection_timeout")):
            with self.subTest(expected=expected):
                response = self.create(password)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json()["detail"]["code"], expected)
                self.assertNotIn(password, response.text)

    def test_session_expiry_and_delete_do_not_revoke_started_lease(self):
        created = self.create().json()
        session_id = created["session_id"]
        lease = self.store.acquire(session_id)
        runner = lease._session.runner
        self.now[0] += 1801
        with self.assertRaisesRegex(SessionError, "expired"):
            self.store.acquire(session_id)
        self.assertFalse(runner.closed)
        self.assertIn("session-password-sentinel", lease.secret_values())
        self.assertEqual(self.client.delete("/api/ssh/sessions/" + session_id).status_code, 200)
        self.assertFalse(runner.closed)
        lease.release()
        self.assertTrue(runner.closed)

    def test_multi_session_password_and_connection_isolation(self):
        first = self.create("first-session-secret").json()["session_id"]
        second = self.create("second-session-secret").json()["session_id"]
        a, b = self.store.acquire(first), self.store.acquire(second)
        try:
            self.assertIsNot(a._session.runner, b._session.runner)
            self.assertIn("first-session-secret", a.secret_values())
            self.assertNotIn("second-session-secret", a.secret_values())
            self.assertIn("second-session-secret", b.secret_values())
            self.store.delete(first)
            self.assertFalse(b._session.runner.closed)
        finally:
            a.release()
            b.release()

    def test_remote_missing_driver_partial_unknown_and_override(self):
        created = self.create().json()
        lease = self.store.acquire(created["session_id"])
        runner = lease._session.runner
        try:
            runner.devices = []
            response = self.client.post("/api/hardware/resolve", json={"ssh_session_id": created["session_id"]})
            self.assertEqual(response.json()["detection_status"], "failed")
            self.assertIsNone(response.json()["hardware"]["fields"]["max_threads_per_block"]["value"])
            runner.devices = [device()]
            response = self.client.post("/api/hardware/resolve", json={"ssh_session_id": created["session_id"], "user_overrides": {"wave_size": 32}})
            public = response.json()
            self.assertEqual(public["detection_status"], "partial")
            fields = public["hardware"]["fields"]
            self.assertEqual(fields["wave_size"]["value"], 32)
            self.assertEqual(fields["wave_size"]["source"], "user_override")
            self.assertIsNone(fields["sm_count"]["value"])
            self.assertIsNone(fields["cu_count"]["value"])
            self.assertEqual(fields["total_memory_bytes"]["unit"], "bytes")
        finally:
            lease.release()

    def test_multi_gpu_selection_device_change_and_bound_execution(self):
        created = self.create().json()
        lease = self.store.acquire(created["session_id"])
        runner = lease._session.runner
        runner.devices = [device(0), device(1)]
        try:
            response = lease.resolve(None)
            self.assertIsNone(response["selected_device_id"])
            lease.select(device(1)["device_id"], {})
            lease.prepare_task("task-test")
            self.assertEqual(runner.execution_env, {"MACA_VISIBLE_DEVICES": "GPU-test-1"})
            runner.devices[1] = device(1, "GPU-replaced")
            with self.assertRaisesRegex(SessionError, "changed"):
                lease.verify_device()
        finally:
            lease.release()

    def test_422_does_not_echo_any_credential_or_input(self):
        sentinel = "never-echo-secret"
        invalid = {"ssh": {"host": "example.invalid", "port": 0, "password": sentinel}, "password": sentinel, "api_key": sentinel, "token": sentinel}
        response = self.client.post("/api/ssh/sessions", json=invalid)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sentinel, response.text)
        self.assertNotIn('"input"', response.text)
        self.assertNotIn('"ctx"', response.text)
        for endpoint, payload in (
            ("/api/settings", {"ssh": {"password": sentinel}, "llm": {"api_key": sentinel}}),
            ("/api/hardware/resolve", {"user_overrides": {"token": sentinel}}),
            ("/api/tasks", {"sample": {"inline_text": "VALUE=1"}, "target": {"user_overrides": {"secret": sentinel}}}),
        ):
            rejected = self.client.post(endpoint, json=payload)
            self.assertEqual(rejected.status_code, 422)
            self.assertNotIn(sentinel, rejected.text)

    def test_expired_or_changed_session_cannot_create_workspace(self):
        session_id = self.create().json()["session_id"]
        manager = TaskManager(self.root / "tasks")
        request = {"ssh_session_id": session_id, "runner": {"type": "ssh"}, "sample": {"inline_text": "VALUE=1"}, "target": {"device_id": device()["device_id"]}}
        self.client.post("/api/hardware/resolve", json={"ssh_session_id": session_id})
        lease = self.store.acquire(session_id)
        lease._session.runner.devices = [device(0, "GPU-changed")]
        lease.release()
        with patch.object(server, "manager", manager), patch.object(server.worker, "submit") as submit:
            response = self.client.post("/api/tasks", json=request)
            self.assertEqual(response.json()["detail"]["code"], "selected_device_changed")
            self.assertEqual(list(manager.tasks_root.iterdir()), [])
            self.now[0] += 1801
            response = self.client.post("/api/tasks", json=request)
            self.assertEqual(response.status_code, 410)
            submit.assert_not_called()
            self.assertEqual(list(manager.tasks_root.iterdir()), [])

    def test_device_selection_without_session_is_not_silently_ignored(self):
        response = self.client.post("/api/tasks", json={"runner": {"type": "ssh"}, "sample": {"inline_text": "VALUE=1"}, "target": {"device_id": "GPU-selected"}})
        self.assertEqual(response.status_code, 422)
        self.assertIn("device_id requires an SSH session", response.text)

    def test_request_origin_and_bind_are_local_only(self):
        self.assertEqual(self.client.get("/api/health", headers={"Origin": "https://evil.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/health", headers={"Host": "evil.invalid"}).status_code, 403)
        for host in ("0.0.0.0", "::", "10.1.2.3", ""):
            with self.assertRaisesRegex(ValueError, "loopback"):
                validate_loopback_host(host)
        self.assertEqual(validate_loopback_host("LOCALHOST"), "localhost")

    def test_server_cli_rejects_public_bind_without_traceback(self):
        result = subprocess.run([sys.executable, "-m", "kernel_opt_agent.server.app", "--host", "0.0.0.0"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_http_session_to_worker_uses_one_connection_without_env(self):
        def close_task_logs():
            for handler in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(handler)
                handler.close()

        self.addCleanup(close_task_logs)
        session_id = self.create().json()["session_id"]
        manager = TaskManager(self.root / "tasks")
        worker = JobWorker(manager)
        with patch.object(server, "manager", manager), patch.object(server, "worker", worker):
            response = self.client.post("/api/hardware/resolve", json={"ssh_session_id": session_id})
            self.assertEqual(response.status_code, 200)
            response = self.client.post("/api/tasks", json={"ssh_session_id": session_id, "runner": {"type": "ssh"},
                "sample": {"source_type": "inline", "inline_text": "VALUE = 1\n"}, "target": {"device_id": device()["device_id"]},
                "profiler": {"enabled": False}, "commands": {"build_command": "python3 -m py_compile kernel.py", "correctness_command": "python3 kernel.py", "benchmark_command": "python3 kernel.py"}})
            self.assertEqual(response.status_code, 200, response.text)
            task_id = response.json()["task_id"]
            for _ in range(100):
                task = manager.get(task_id)
                if task.status in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            self.assertEqual(task.status, "completed", task.error)
            result_dir = Path(task.results_dir)
            self.assertTrue((result_dir / "best_kernel.py").is_file())
            self.assertTrue((result_dir / "hardware_snapshot.yaml").is_file())
            lease = self.store.acquire(session_id)
            runner = lease._session.runner
            lease.release()
            self.assertEqual(runner.connect_count, 1)
            self.assertGreater(runner.upload_count, 0)
            self.assertTrue((result_dir / "remote_artifacts/download.txt").exists())
            stages = [name for name, _, _ in runner.commands]
            self.assertLess(stages.index("correctness"), stages.index("benchmark"))
            self.assertTrue(all(env == {"MACA_VISIBLE_DEVICES": "GPU-test-0"} for name, env, _ in runner.commands if name in {"build", "correctness", "benchmark"}))
            for path in Path(task.workspace).rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"session-password-sentinel", path.read_bytes(), str(path))
                    self.assertNotIn(session_id.encode(), path.read_bytes(), str(path))

    def test_host_key_confirmation_mismatch_and_changes(self):
        store = HostKeyStore(self.root / "hosts.json")
        with self.assertRaisesRegex(HostKeyError, "confirmation_required"):
            store.verify("example.invalid", 22, "SHA256:first", None)
        self.assertFalse(store.path.exists())
        with self.assertRaisesRegex(HostKeyError, "mismatch"):
            store.verify("example.invalid", 22, "SHA256:first", "SHA256:wrong")
        store.remember("example.invalid", 22, "SHA256:first", "SHA256:first")
        store.verify("example.invalid", 22, "SHA256:first", None)
        with self.assertRaisesRegex(HostKeyError, "changed"):
            store.verify("example.invalid", 22, "SHA256:second", "SHA256:second")
        self.assertNotIn("example.invalid", store.path.read_text())

    def test_source_optimizer_reuses_session_runner_through_rollback(self):
        from kernel_opt_agent.server.models import TaskCreateRequest

        def close_logs():
            for handler in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(handler)
                handler.close()

        self.addCleanup(close_logs)
        source = '''import tilelang.language as T
@T.prim_func
def kernel(A: T.Tensor([8], "float32")):
    shared = T.alloc_shared([8], "float32")
    for i in T.Parallel(8):
        shared[i] = A[i]
'''
        session_id = self.create().json()["session_id"]
        lease = self.store.acquire(session_id)
        with lease.operation():
            lease.select(device()["device_id"], {})
        request = TaskCreateRequest.model_validate({"runner": {"type": "ssh"}, "ssh_session_id": session_id,
            "target": {"device_id": device()["device_id"]}, "sample": {"inline_text": source},
            "profiler": {"enabled": False}, "optimization": {"enabled": True, "max_candidates": 1, "benchmark_repeats": 2}})
        manager = TaskManager(self.root / "tasks")
        task = manager.create(request)
        worker = JobWorker(manager)
        worker._run_task(task.task_id, request, lease)
        task = manager.get(task.task_id)
        self.assertEqual(task.status, "completed", task.error)
        result = json.loads((Path(task.results_dir) / "source_optimization.json").read_text())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["trials"]), 1)
        self.assertTrue(result["trials"][0]["rollback_verified"])
        self.assertEqual(lease._session.runner.connect_count, 1)

    def test_predefined_query_is_guarded_and_runner_exceptions_degrade(self):
        self.assertTrue(validate(GPU_QUERY_COMMAND, "/tmp/managed", "/tmp/managed").allowed)
        runner = Mock()
        runner.run_detection.side_effect = RuntimeError("credential-secret")
        result = query_remote_devices(runner)
        self.assertEqual(result["devices"], [])
        self.assertNotIn("credential-secret", json.dumps(result))

    def test_password_context_reaches_paramiko_and_key_auth_stays_supported(self):
        for auth_type in ("password", "key"):
            info = SSHConnectionInfo("example.invalid", 22, "user", auth_type, "test-key", "UNSET_TEST_PASSWORD", "/tmp/managed-test")
            runner = SSHRunner(info, 5, password=SecretStr("context-only-password"))
            client = Mock()
            with patch("paramiko.SSHClient", return_value=client), patch.object(runner, "_mkdir_p"), patch.object(runner, "_ensure_workspace_marker"):
                runner.connect()
            arguments = client.connect.call_args.kwargs
            if auth_type == "password":
                self.assertEqual(arguments["password"], "context-only-password")
                self.assertFalse(arguments["allow_agent"])
            else:
                self.assertEqual(arguments["key_filename"], "test-key")
                self.assertNotIn("password", arguments)
            self.assertIn("auth_timeout", arguments)
            self.assertNotIsInstance(client.set_missing_host_key_policy.call_args.args[0], paramiko.AutoAddPolicy)

    def test_cuda_and_metax_properties_never_confuse_sm_with_cu(self):
        properties = SimpleNamespace(name="MetaX C500", uuid="GPU-test", pci_bus_id=16, total_memory=1024,
                                     warp_size=64, multi_processor_count=104, shared_memory_per_block=65536)
        cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, get_device_properties=lambda index: properties,
                               mem_get_info=lambda index: (512, 1024))
        torch = SimpleNamespace(__version__="test", cuda=cuda, version=SimpleNamespace(maca="3.3", cuda=None))
        with patch.dict("sys.modules", {"torch": torch}), patch("kernel_opt_agent.hardware.remote_gpu_query.shutil.which", return_value=None), patch("kernel_opt_agent.hardware.remote_gpu_query.os.path.isfile", return_value=False):
            metax = collect_gpu_query()["devices"][0]["fields"]
            self.assertIsNone(metax["sm_count"])
            self.assertIsNone(metax["cu_count"])
            self.assertEqual(metax["runtime_multiprocessor_count"], 104)
            self.assertEqual(metax["warp_size"], 64)
            self.assertIsNone(metax["wave_size"])
            self.assertIsNone(metax["max_threads_per_block"])
            self.assertIsNone(metax["pci_bus_id"])
            properties.name = "NVIDIA test"
            torch.version.maca = None
            torch.version.cuda = "12.0"
            nvidia = collect_gpu_query()["devices"][0]["fields"]
            self.assertEqual(nvidia["sm_count"], 104)
            self.assertIsNone(nvidia["cu_count"])

    def test_runner_guard_applies_to_device_commands_and_predefined_query(self):
        runner = SSHRunner(SSHConnectionInfo("example.invalid", 22, "user", "key", "test-key", None, "/tmp/managed-test"), 5)
        runner.client = Mock()
        runner.execution_env = {"MACA_VISIBLE_DEVICES": "GPU-test"}
        result = runner.run("build", "export MACA_VISIBLE_DEVICES=1; python3 kernel.py")
        self.assertTrue(result.guard_denied)
        runner.client.exec_command.assert_not_called()
        with self.assertRaisesRegex(ValueError, "predefined"):
            runner.run_bound_detection("invalid", "python3 malicious.py")


if __name__ == "__main__":
    unittest.main()
