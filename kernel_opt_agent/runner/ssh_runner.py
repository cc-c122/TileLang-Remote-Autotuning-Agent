from __future__ import annotations

import os
import hashlib
import posixpath
import shlex
import stat
import time
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from .host_keys import HostKeyStore, key_fingerprint

from .command_guard import validate
from .local_runner import CommandResult


@dataclass
class SSHConnectionInfo:
    host: str
    port: int
    username: str
    auth_type: str
    key_path: str | None
    password_env: str | None
    remote_workspace: str
    host_key_fingerprint: str | None = None
    connect_timeout_seconds: int = 15


class SSHRunner:
    MARKER_FILENAME = ".kernel_opt_agent_workspace"
    SYSTEM_PATH_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/sbin", "/sys", "/usr", "/var")

    def __init__(self, info: SSHConnectionInfo, timeout_seconds: int, denied_commands: list[str] | None = None,
                 *, password: SecretStr | None = None, host_key_store: HostKeyStore | None = None):
        self.info = info
        self.timeout_seconds = timeout_seconds
        self.denied_commands = denied_commands or []
        self.client = None
        self.sftp = None
        self._password = password
        self.host_key_store = host_key_store or HostKeyStore()
        self.execution_env: dict[str, str] = {}
        self.server_fingerprint: str | None = None
        self.python_bin_dir: str | None = None

    def redact(self, value: str) -> str:
        secrets = [self._password.get_secret_value()] if self._password else []
        env_password = os.environ.get(self.info.password_env or "")
        if env_password:
            secrets.append(env_password)
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<redacted:secret>")
        return value

    def _password_from_env(self) -> str:
        if self.info.auth_type != "password":
            return ""
        if self._password is not None:
            return self._password.get_secret_value()
        if not self.info.password_env:
            raise ValueError("SSH password authentication requires remote.password_env")
        password = os.environ.get(self.info.password_env)
        if not password:
            raise ValueError(f"SSH password env var is not set: {self.info.password_env}")
        return password

    def connect(self) -> None:
        if not self._is_safe_workspace_to_clear(self.info.remote_workspace):
            raise ValueError("unsafe remote workspace")
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SSH runner requires the optional dependency 'paramiko'. Install dependencies with: pip install -r kernel_opt_agent/requirements.txt") from exc

        runner = self

        class VerifiedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fingerprint = key_fingerprint(key)
                runner.host_key_store.verify(runner.info.host, runner.info.port, fingerprint, runner.info.host_key_fingerprint)
                runner.server_fingerprint = fingerprint

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(VerifiedHostKeyPolicy())
        if self.info.auth_type == "password":
            self.client.connect(
                hostname=self.info.host,
                port=self.info.port,
                username=self.info.username,
                password=self._password_from_env(),
                timeout=self.info.connect_timeout_seconds,
                auth_timeout=self.info.connect_timeout_seconds,
                banner_timeout=self.info.connect_timeout_seconds,
                look_for_keys=False,
                allow_agent=False,
            )
        else:
            key_path = os.path.expanduser(self.info.key_path or "")
            self.client.connect(
                hostname=self.info.host,
                port=self.info.port,
                username=self.info.username,
                key_filename=key_path,
                timeout=self.info.connect_timeout_seconds,
                auth_timeout=self.info.connect_timeout_seconds,
                banner_timeout=self.info.connect_timeout_seconds,
                look_for_keys=True,
                allow_agent=True,
            )
        if self.server_fingerprint:
            self.host_key_store.remember(self.info.host, self.info.port, self.server_fingerprint, self.info.host_key_fingerprint)
        transport = self.client.get_transport()
        if transport:
            transport.set_keepalive(30)
        self.sftp = self.client.open_sftp()
        self.sftp.get_channel().settimeout(self.info.connect_timeout_seconds)
        self._mkdir_p(self.info.remote_workspace)
        self._ensure_workspace_marker(self.info.remote_workspace)

    def close(self) -> None:
        for resource in (self.sftp, self.client):
            if resource:
                try:
                    resource.close()
                except Exception:
                    pass
        self._password = None
        self.sftp = None
        self.client = None

    def _mkdir_p(self, remote_path: str) -> None:
        assert self.sftp is not None
        parts = remote_path.strip("/").split("/")
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}"
            try:
                item = self.sftp.lstat(cur)
                if stat.S_ISLNK(item.st_mode):
                    raise ValueError("remote workspace must not contain symlinks")
            except OSError:
                self.sftp.mkdir(cur)

    def _is_safe_workspace_to_clear(self, remote_path: str) -> bool:
        normalized = posixpath.normpath(remote_path)
        if not normalized.startswith("/"):
            return False
        if normalized in {"/", "/tmp", "/home", "/workspace"}:
            return False
        if len(normalized.strip("/").split("/")) < 2:
            return False
        return not any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in self.SYSTEM_PATH_PREFIXES)

    def _marker_path(self, remote_path: str) -> str:
        return posixpath.join(posixpath.normpath(remote_path), self.MARKER_FILENAME)

    def _has_workspace_marker(self, remote_path: str) -> bool:
        assert self.sftp is not None
        try:
            marker = self.sftp.stat(self._marker_path(remote_path))
        except OSError:
            return False
        return not stat.S_ISDIR(marker.st_mode)

    def _write_workspace_marker(self, remote_path: str) -> None:
        assert self.sftp is not None
        marker_path = self._marker_path(remote_path)
        with self.sftp.open(marker_path, "w") as marker:
            marker.write("kernel_opt_agent managed workspace\n")

    def _ensure_workspace_marker(self, remote_path: str) -> None:
        assert self.sftp is not None
        if not self._is_safe_workspace_to_clear(remote_path):
            raise RuntimeError(f"refusing unsafe remote workspace: {remote_path}")
        if self._has_workspace_marker(remote_path):
            return
        entries = self.sftp.listdir_attr(remote_path)
        if entries:
            raise RuntimeError(f"remote workspace is not marked and is not empty: {remote_path}")
        self._write_workspace_marker(remote_path)

    def _clear_dir_contents(self, remote_path: str) -> None:
        assert self.sftp is not None
        if not self._is_safe_workspace_to_clear(remote_path):
            raise RuntimeError(f"refusing to clear unsafe remote workspace: {remote_path}")
        if not self._has_workspace_marker(remote_path):
            raise RuntimeError(f"refusing to clear unmarked remote workspace: {remote_path}")
        try:
            entries = self.sftp.listdir_attr(remote_path)
        except OSError:
            self._mkdir_p(remote_path)
            self._write_workspace_marker(remote_path)
            return
        for entry in entries:
            if entry.filename == self.MARKER_FILENAME:
                continue
            child = posixpath.join(remote_path, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                self._remove_dir(child)
            else:
                self.sftp.remove(child)
        self._write_workspace_marker(remote_path)

    def _remove_dir(self, remote_dir: str) -> None:
        assert self.sftp is not None
        for entry in self.sftp.listdir_attr(remote_dir):
            child = posixpath.join(remote_dir, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                self._remove_dir(child)
            else:
                self.sftp.remove(child)
        self.sftp.rmdir(remote_dir)

    def upload(self, local_path: Path, remote_path: str | None = None) -> None:
        assert self.sftp is not None
        remote_path = remote_path or self.info.remote_workspace
        normalized = posixpath.normpath(remote_path)
        root = posixpath.normpath(self.info.remote_workspace)
        if normalized != root and not normalized.startswith(root + "/"):
            raise ValueError("upload path must stay inside managed workspace")
        local_path = local_path.resolve()
        if posixpath.normpath(remote_path) == posixpath.normpath(self.info.remote_workspace):
            self._clear_dir_contents(remote_path)
        if local_path.is_file():
            self._mkdir_p(posixpath.dirname(remote_path))
            self.sftp.put(str(local_path), remote_path)
            return
        self._mkdir_p(remote_path)
        for item in local_path.rglob("*"):
            rel = item.relative_to(local_path).as_posix()
            dest = posixpath.join(remote_path, rel)
            if item.is_dir():
                self._mkdir_p(dest)
            else:
                self._mkdir_p(posixpath.dirname(dest))
                self.sftp.put(str(item), dest)

    def download_artifacts(self, local_results: Path) -> None:
        assert self.sftp is not None
        local_results.mkdir(parents=True, exist_ok=True)
        for name in ("results", "logs", "best_kernel.py", "best_config.yaml"):
            remote = posixpath.join(self.info.remote_workspace, name)
            try:
                st = self.sftp.lstat(remote)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                raise ValueError("artifact symlinks are not allowed")
            if stat.S_ISDIR(st.st_mode):
                self._download_dir(remote, local_results / name)
            else:
                self.sftp.get(remote, str(local_results / name))

    def file_sha256(self, relative_path: str) -> str:
        assert self.sftp is not None
        normalized = posixpath.normpath(relative_path)
        if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"execution file must stay inside remote workspace: {relative_path}")
        remote_path = posixpath.join(self.info.remote_workspace, normalized)
        current = self.info.remote_workspace
        for part in normalized.split("/"):
            current = posixpath.join(current, part)
            if stat.S_ISLNK(self.sftp.lstat(current).st_mode):
                raise ValueError("execution artifacts must not contain symlinks")
        digest = hashlib.sha256()
        with self.sftp.open(remote_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _download_dir(self, remote_dir: str, local_dir: Path) -> None:
        assert self.sftp is not None
        local_dir.mkdir(parents=True, exist_ok=True)
        for entry in self.sftp.listdir_attr(remote_dir):
            if entry.filename in {".", ".."} or any(char in entry.filename for char in ("/", "\\", ":")):
                raise ValueError("invalid remote artifact filename")
            if stat.S_ISLNK(entry.st_mode):
                raise ValueError("artifact symlinks are not allowed")
            remote = posixpath.join(remote_dir, entry.filename)
            local = local_dir / entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self._download_dir(remote, local)
            else:
                self.sftp.get(remote, str(local))

    def _build_remote_command(self, command: str, *, device_bound: bool = True) -> str:
        prefix = ""
        if device_bound and self.python_bin_dir:
            prefix = f'export PATH={shlex.quote(self.python_bin_dir)}:"$PATH" && '
        if device_bound and self.execution_env:
            values = " ".join(f"{name}={shlex.quote(value)}" for name, value in sorted(self.execution_env.items()))
            prefix += f"export {values} && "
        return f"cd {shlex.quote(self.info.remote_workspace)} && {prefix}{command}"

    def run(self, name: str, command: str | None) -> CommandResult:
        return self._run(name, command, device_bound=True)

    def run_detection(self, name: str, command: str) -> CommandResult:
        return self._run(name, command, device_bound=False)

    def run_bound_detection(self, name: str, command: str) -> CommandResult:
        from kernel_opt_agent.hardware.remote_gpu_detector import GPU_QUERY_COMMAND

        if command != GPU_QUERY_COMMAND:
            raise ValueError("only the predefined GPU query is allowed")
        return self._run(name, command, device_bound=True, predefined_query=True)

    def _run(self, name: str, command: str | None, *, device_bound: bool, predefined_query: bool = False) -> CommandResult:
        assert self.client is not None
        start = time.time()
        if not command:
            return CommandResult(name, "", 0, "", "", start, start, 0.0)
        guard = validate(command, self.info.remote_workspace, self.info.remote_workspace, self.denied_commands)
        if device_bound and not predefined_query and self.execution_env and re.search(r"\b(?:CUDA|MACA|HIP|MUSA)_VISIBLE_DEVICES\b", command):
            guard.allowed = False
            guard.reason = "selected device visibility cannot be overridden by task commands"
        if not guard.allowed:
            end = time.time()
            return CommandResult(name, command, 126, "", guard.reason, start, end, end - start, guard_denied=True, error_message=guard.reason)
        remote_command = self._build_remote_command(command, device_bound=device_bound)
        try:
            stdin, stdout, stderr = self.client.exec_command(remote_command, timeout=self.timeout_seconds)
            channel = stdout.channel
            out_parts: list[bytes] = []
            err_parts: list[bytes] = []
            output_bytes = 0
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                for ready, receive, chunks in (
                    (channel.recv_ready, channel.recv, out_parts),
                    (channel.recv_stderr_ready, channel.recv_stderr, err_parts),
                ):
                    if ready():
                        chunk = receive(65536)
                        output_bytes += len(chunk)
                        if output_bytes > 8 * 1024 * 1024:
                            channel.close()
                            raise RuntimeError("remote output limit exceeded")
                        chunks.append(chunk)
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if time.monotonic() > deadline:
                    channel.close()
                    end = time.time()
                    return CommandResult(name, command, 124, self.redact(b"".join(out_parts).decode("utf-8", errors="replace")), self.redact(b"".join(err_parts).decode("utf-8", errors="replace")), start, end, end-start, timeout=True, error_message="remote command timed out")
                time.sleep(0.01)
            out = self.redact(b"".join(out_parts).decode("utf-8", errors="replace"))
            err = self.redact(b"".join(err_parts).decode("utf-8", errors="replace"))
            rc = channel.recv_exit_status()
            end = time.time()
            return CommandResult(name, command, rc, out, err, start, end, end - start)
        except Exception as exc:
            end = time.time()
            message = self.redact(f"remote command failed: {type(exc).__name__}")
            return CommandResult(name, command, 1, "", message, start, end, end - start, error_message=message)
