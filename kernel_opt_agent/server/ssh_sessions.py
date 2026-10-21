from __future__ import annotations

import secrets
import threading
import time
import posixpath
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from pydantic import SecretStr

from kernel_opt_agent.hardware.remote_gpu_detector import device_signature, query_remote_devices, resolve_remote_hardware
from kernel_opt_agent.runner.host_keys import HostKeyStore
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner
from kernel_opt_agent.redaction import register_log_secrets, unregister_log_secrets

from .models import SSHSessionRequest, SSHSettings


class SessionError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(repr=False)
class _Session:
    runner: SSHRunner
    settings: SSHSettings
    expires: float
    password: SecretStr | None = field(repr=False)
    credential_id: SecretStr | None = field(default=None, repr=False)
    connection_id: str = field(default_factory=lambda: "conn_" + secrets.token_hex(12))
    refs: int = 0
    revoked: bool = False
    operation_lock: Any = field(default_factory=threading.RLock)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    log_secrets_registered: bool = True


class _BorrowedRunner:
    def __init__(self, runner: SSHRunner, redactor=None):
        self._runner = runner
        self._redactor = redactor

    def __getattr__(self, name):
        return getattr(self._runner, name)

    def close(self):
        # The lease owns the authenticated connection across all task stages.
        pass

    def download_artifacts(self, results_path):
        self._runner.download_artifacts(results_path)
        if self._redactor:
            for path in results_path.rglob("*"):
                if not path.is_file():
                    continue
                path.resolve().relative_to(results_path.resolve())
                content = path.read_bytes()
                text = content.decode("utf-8", errors="replace")
                sanitized = self._redactor(text)
                if sanitized != text:
                    path.write_text(sanitized, encoding="utf-8")


class SessionLease:
    def __init__(self, store: "SSHSessionStore", session: _Session):
        self._store = store
        self._session = session
        self._released = False
        self.device: dict[str, Any] | None = None
        self.hardware_snapshot: dict[str, Any] | None = None
        self.remote_workspace = session.settings.remote_workspace
        self._extra_redaction_values: list[str] = []

    def add_redaction_values(self, values: list[str]) -> None:
        self._extra_redaction_values.extend(value for value in values if value)

    def secret_values(self) -> list[str]:
        return [value.get_secret_value() for value in (self._session.password, self._session.credential_id) if value is not None] + self._extra_redaction_values

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            value = self._session.runner.redact(value)
            for secret in self.secret_values():
                value = value.replace(secret, "<redacted:secret>")
            return value
        return value

    @contextmanager
    def operation(self):
        with self._session.operation_lock:
            yield self

    def resolve(self, device_id: str | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        self._session.runner.timeout_seconds = 60
        payload = self.redact(query_remote_devices(self._session.runner))
        response = resolve_remote_hardware(payload, device_id, overrides)
        response["connection_id"] = self._session.connection_id
        self._environment = dict(payload.get("environment", {}))
        self._session.devices = {item["device_id"]: item for item in payload["devices"]}
        response = self.redact(response)
        detection_id = "detection_" + secrets.token_hex(12)
        response["detection_id"] = detection_id
        directory = self._store.evidence_root / detection_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "hardware_snapshot.json").write_text(json.dumps(response, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        (directory / "hardware_detection.log").write_text("\n".join([response["detection_status"], *response["warnings"]]) + "\n", encoding="utf-8")
        return response

    def select(self, device_id: str | None, overrides: dict[str, Any]) -> dict[str, Any]:
        previous = dict(self._session.devices)
        response = self.resolve(device_id, overrides)
        selected_id = response["selected_device_id"]
        if device_id and previous and device_id not in self._session.devices:
            raise SessionError("selected_device_changed")
        if selected_id is None:
            raise SessionError("selected_device_not_found" if device_id and response["devices"] else "device_selection_required" if response["devices"] else "gpu_detection_failed")
        device = self._session.devices[selected_id]
        if not device.get("identity_verified"):
            raise SessionError("stable_device_identity_unavailable")
        if device_id and previous and (device_id not in previous or device_signature(previous[device_id]) != device_signature(device)):
            raise SessionError("selected_device_changed")
        self.device = dict(device)
        self.hardware_snapshot = response
        return response

    def verify_device(self) -> None:
        if self.device is None:
            raise SessionError("device_selection_required")
        payload = query_remote_devices(self._session.runner)
        current = next((item for item in payload["devices"] if item.get("device_id") == self.device["device_id"]), None)
        if current is None or device_signature(current) != device_signature(self.device):
            raise SessionError("selected_device_changed")

    def prepare_task(self, task_id: str) -> None:
        self.verify_device()
        runner = self._session.runner
        self.remote_workspace = self._session.settings.remote_workspace.rstrip("/") + "/tasks/" + task_id
        runner.info.remote_workspace = self.remote_workspace
        runner._mkdir_p(self.remote_workspace)
        runner._ensure_workspace_marker(self.remote_workspace)
        device = self.device or {}
        variable = "MACA_VISIBLE_DEVICES" if device.get("backend") == "mxmaca" else "CUDA_VISIBLE_DEVICES"
        runner.execution_env = {variable: str(device["selector"])}
        interpreter = getattr(self, "_environment", {}).get("python_executable")
        if interpreter and posixpath.isabs(interpreter):
            runner.python_bin_dir = posixpath.dirname(interpreter)
        self.verify_bound_device()
        if self.hardware_snapshot is not None:
            self.hardware_snapshot["execution_device_verified"] = True
            self.hardware_snapshot["execution_visible_index"] = 0

    def verify_bound_device(self) -> None:
        payload = query_remote_devices(self._session.runner, device_bound=True)
        devices = payload["devices"]
        if len(devices) != 1 or devices[0].get("device_id") != (self.device or {}).get("device_id"):
            raise SessionError("execution_device_mismatch")

    def build_runner(self, config, trial_dir):
        from kernel_opt_agent.redaction import redact_data

        runner = self._session.runner
        self.verify_bound_device()
        runner.timeout_seconds = config.search.timeout_seconds
        runner.denied_commands = config.constraints.denied_commands
        runner.upload(trial_dir)
        return _BorrowedRunner(runner, lambda value: redact_data(config, value))

    def detection_runner(self):
        return _BorrowedRunner(self._session.runner)

    def apply_settings(self, config) -> None:
        ssh = self._session.settings
        for name in ("host", "port", "username", "auth_type", "password_env", "key_path", "host_key_fingerprint"):
            setattr(config.remote, name, getattr(ssh, name))
        config.remote.remote_workspace = self.remote_workspace
        if self.device is not None:
            config.hardware.backend = self.device["backend"]
            config.hardware.target_name = self.device.get("fields", {}).get("target_name")

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._store._release(self._session)


class SSHSessionStore:
    def __init__(self, host_key_store: HostKeyStore | None = None, *, ttl_seconds: int = 1800, clock=time.time, runner_factory=SSHRunner, evidence_root: Path | None = None):
        self.host_key_store = host_key_store or HostKeyStore()
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.runner_factory = runner_factory
        self.evidence_root = evidence_root or Path(__file__).resolve().parents[1] / "workspace/server_runtime/hardware"
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def create(self, request: SSHSessionRequest) -> dict[str, Any]:
        ssh = request.ssh.model_copy(deep=True)
        if not ssh.host.strip() or not ssh.username.strip():
            raise SessionError("ssh_host_and_username_required", 422)
        if ssh.auth_type == "password" and not request.password:
            raise SessionError("password_required", 422)
        if ssh.auth_type == "key" and not ssh.key_path:
            raise SessionError("key_path_required", 422)
        info = SSHConnectionInfo(ssh.host, ssh.port, ssh.username, ssh.auth_type, ssh.key_path, ssh.password_env,
                                 ssh.remote_workspace, ssh.host_key_fingerprint, ssh.connect_timeout_seconds)
        runner = self.runner_factory(info, min(60, ssh.connect_timeout_seconds), password=request.password, host_key_store=self.host_key_store)
        password_values = [request.password.get_secret_value()] if request.password else []
        register_log_secrets(password_values)
        try:
            runner.connect()
        except Exception:
            runner.close()
            unregister_log_secrets(password_values)
            raise
        session_id = secrets.token_urlsafe(32)
        register_log_secrets([session_id])
        session = _Session(runner, ssh, self.clock() + self.ttl_seconds, request.password, SecretStr(session_id))
        with self._lock:
            self._sessions[session_id] = session
        timer = threading.Timer(self.ttl_seconds, self._expire, args=(session_id,))
        timer.daemon = True
        timer.start()
        return {"ok": True, "session_id": session_id, "expires_at": datetime.fromtimestamp(session.expires, timezone.utc).isoformat()}

    def acquire(self, session_id: str) -> SessionLease:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.revoked:
                raise SessionError("ssh_session_not_found", 404)
            if self.clock() >= session.expires:
                self._retire(session)
                raise SessionError("ssh_session_expired", 410)
            session.refs += 1
            return SessionLease(self, session)

    def _retire(self, session: _Session) -> None:
        if session.refs == 0:
            session.runner.close()
            if session.log_secrets_registered:
                unregister_log_secrets([value.get_secret_value() for value in (session.password, session.credential_id) if value])
                session.log_secrets_registered = False
            session.password = None
            session.credential_id = None

    def _expire(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and self.clock() >= session.expires:
                self._retire(session)

    def _release(self, session: _Session) -> None:
        with self._lock:
            session.refs -= 1
            if session.revoked or self.clock() >= session.expires:
                self._retire(session)

    def delete(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is not None:
                session.revoked = True
                self._retire(session)
