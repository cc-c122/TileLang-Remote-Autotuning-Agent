from __future__ import annotations

import os
import hashlib
import posixpath
import shlex
import stat
import time
from dataclasses import dataclass
from pathlib import Path

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


class SSHRunner:
    MARKER_FILENAME = ".kernel_opt_agent_workspace"
    SYSTEM_PATH_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/sbin", "/sys", "/usr", "/var")

    def __init__(self, info: SSHConnectionInfo, timeout_seconds: int, denied_commands: list[str] | None = None):
        self.info = info
        self.timeout_seconds = timeout_seconds
        self.denied_commands = denied_commands or []
        self.client = None
        self.sftp = None

    def _password_from_env(self) -> str:
        if self.info.auth_type != "password":
            return ""
        if not self.info.password_env:
            raise ValueError("SSH password authentication requires remote.password_env")
        password = os.environ.get(self.info.password_env)
        if not password:
            raise ValueError(f"SSH password env var is not set: {self.info.password_env}")
        return password

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SSH runner requires the optional dependency 'paramiko'. Install dependencies with: pip install -r kernel_opt_agent/requirements.txt") from exc

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if self.info.auth_type == "password":
            self.client.connect(
                hostname=self.info.host,
                port=self.info.port,
                username=self.info.username,
                password=self._password_from_env(),
                timeout=20,
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
                timeout=20,
                look_for_keys=True,
                allow_agent=True,
            )
        self.sftp = self.client.open_sftp()
        self._mkdir_p(self.info.remote_workspace)
        self._ensure_workspace_marker(self.info.remote_workspace)

    def close(self) -> None:
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()

    def _mkdir_p(self, remote_path: str) -> None:
        assert self.sftp is not None
        parts = remote_path.strip("/").split("/")
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}"
            try:
                self.sftp.stat(cur)
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
                st = self.sftp.stat(remote)
            except OSError:
                continue
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
        digest = hashlib.sha256()
        with self.sftp.open(remote_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _download_dir(self, remote_dir: str, local_dir: Path) -> None:
        assert self.sftp is not None
        local_dir.mkdir(parents=True, exist_ok=True)
        for entry in self.sftp.listdir_attr(remote_dir):
            remote = posixpath.join(remote_dir, entry.filename)
            local = local_dir / entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self._download_dir(remote, local)
            else:
                self.sftp.get(remote, str(local))

    def _build_remote_command(self, command: str) -> str:
        return f"cd {shlex.quote(self.info.remote_workspace)} && {command}"

    def run(self, name: str, command: str | None) -> CommandResult:
        assert self.client is not None
        start = time.time()
        if not command:
            return CommandResult(name, "", 0, "", "", start, start, 0.0)
        guard = validate(command, self.info.remote_workspace, self.info.remote_workspace, self.denied_commands)
        if not guard.allowed:
            end = time.time()
            return CommandResult(name, command, 126, "", guard.reason, start, end, end - start, guard_denied=True, error_message=guard.reason)
        remote_command = self._build_remote_command(command)
        try:
            stdin, stdout, stderr = self.client.exec_command(remote_command, timeout=self.timeout_seconds)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            rc = stdout.channel.recv_exit_status()
            end = time.time()
            return CommandResult(name, command, rc, out, err, start, end, end - start)
        except Exception as exc:
            end = time.time()
            return CommandResult(name, command, 1, "", str(exc), start, end, end - start, error_message=str(exc))
