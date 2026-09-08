from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kernel_opt_agent.config_model import AppConfig
from kernel_opt_agent.main import run_trial
from kernel_opt_agent.profiler.mcprofiler import (
    COLLECTION_SCHEMA_VERSION,
    McProfilerCollectionRequest,
    collect_remote_mcprofiler_case,
)
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner
from kernel_opt_agent.storage.experiment_db import ExperimentDB


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcprofiler" / "real_v8_tc1_gate"


class FakeRemoteCollectionRunner:
    def __init__(self, remote_root: Path, mode: str = "success", secret: str = ""):
        self.remote_root = remote_root
        self.remote_root.mkdir(parents=True, exist_ok=True)
        self.info = SimpleNamespace(remote_workspace="/managed/mcprofiler-task")
        self.denied_commands = []
        self.mode = mode
        self.secret = secret
        self.commands: list[tuple[str, str]] = []

    @staticmethod
    def _result(
        name: str,
        command: str,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timeout: bool = False,
    ) -> CommandResult:
        now = time.time()
        return CommandResult(name, command, returncode, stdout, stderr, now, now, 0.0, timeout=timeout, error_message=stderr or None)

    def run(self, name: str, command: str) -> CommandResult:
        self.commands.append((name, command))
        if name == "correctness":
            return self._result(name, command, stdout='CORRECTNESS_RESULT status=PASS max_error=0 reason="ok"\n')
        if name == "benchmark":
            return self._result(name, command, stdout="BENCHMARK_RESULT latency_ms=1.25\n")
        if self.mode == "missing_tool" and name == "mcprofiler_client_check":
            return self._result(name, command, 1, stderr="not found")
        return self._result(name, command)

    def run_cancellable(self, name: str, command: str, should_cancel=None, timeout_seconds=None) -> CommandResult:
        self.commands.append((name, command))
        self.collection_timeout = timeout_seconds
        if self.mode == "cancel":
            return self._result(name, command, 130, stderr="command cancelled")
        if self.mode == "timeout":
            return self._result(name, command, 124, stderr="timed out", timeout=True)
        if self.mode == "failed":
            return self._result(name, command, 1, stderr=f"vendor failed {self.secret}")
        if self.mode == "no_report":
            return self._result(name, command)
        match = __import__("re").search(r"\.kernel_opt_agent/mcprofiler/collections/(mcprof-[a-f0-9]+)", command)
        if match is None:
            return self._result(name, command, 1, stderr="controlled collection directory missing")
        case_root = self.remote_root / ".kernel_opt_agent" / "mcprofiler" / "collections" / match.group(1) / "real-case"
        shutil.copytree(FIXTURE, case_root, dirs_exist_ok=True)
        if self.mode == "ambiguous":
            shutil.copytree(FIXTURE, case_root.parent / "second-case", dirs_exist_ok=True)
        if self.mode == "artifact_secret":
            (case_root / "unexpected.log").write_text(self.secret, encoding="utf-8")
        return self._result(name, command)

    def find_files(self, filename: str, relative_root: str = ".") -> list[str]:
        root = self.remote_root / relative_root
        return sorted(path.relative_to(self.remote_root).as_posix() for path in root.rglob(filename))

    def file_sha256(self, relative_path: str) -> str:
        return hashlib.sha256((self.remote_root / relative_path).read_bytes()).hexdigest()

    def download(self, relative_path: str, local_path: Path, max_files=2048, max_bytes=256 * 1024 * 1024) -> None:
        source = self.remote_root / relative_path
        files = [source] if source.is_file() else [path for path in source.rglob("*") if path.is_file()]
        if len(files) > max_files or sum(path.stat().st_size for path in files) > max_bytes:
            raise ValueError("artifact limit exceeded")
        if source.is_dir():
            shutil.copytree(source, local_path, dirs_exist_ok=True)
        else:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, local_path)


class McProfilerCollectionTests(unittest.TestCase):
    def request(self, **kwargs) -> McProfilerCollectionRequest:
        return McProfilerCollectionRequest(
            target_command="python3 benchmark.py --context-length 128",
            case_name="paged-attention-ctx128",
            source_trial_id="source-baseline",
            source_sha256="a" * 64,
            kernel_names=("paged_attention",),
            service_port=52124,
            **kwargs,
        )

    def test_official_remote_collection_imports_real_case_and_hashes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = FakeRemoteCollectionRunner(root / "remote")
            results = root / "results"
            result = collect_remote_mcprofiler_case(runner, results, self.request())
            self.assertEqual(result.schema_version, COLLECTION_SCHEMA_VERSION)
            self.assertEqual(result.status, "collected")
            self.assertEqual(result.reason_category, "none")
            self.assertEqual(result.source_trial_id, "source-baseline")
            self.assertEqual(result.source_sha256, "a" * 64)
            self.assertIsNone(result.shape_id)
            self.assertTrue(result.artifact_manifest)
            self.assertTrue(result.available_metrics["l2c_hit_rate"])
            self.assertTrue(any(item["metric_name"] == "l2c_hit_rate" for item in result.metric_observations))
            self.assertTrue((results / "profiler_results.jsonl").is_file())
            self.assertTrue((results / "metric_observations.jsonl").is_file())
            self.assertTrue((results / "diagnoses.jsonl").is_file())
            self.assertTrue((results / "mcprofiler_collection.jsonl").is_file())
            self.assertTrue((results / result.logs["stdout"]).is_file())
            evidence_rows = [
                json.loads(line)
                for line in (results / "metric_observations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(evidence_rows)
            self.assertTrue(all(row["source_trial_id"] == "source-baseline" for row in evidence_rows))
            for artifact in result.artifact_manifest:
                path = results / result.case_dir / artifact["relative_path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"])
                self.assertEqual(artifact["source"], "mcprofiler_auto_collection")
            collection_command = next(command for name, command in runner.commands if name == "mcprofiler_collection")
            self.assertIn("mcProfiler", collection_command)
            self.assertIn("profiler_server", collection_command)
            self.assertIn("127.0.0.1", collection_command)
            self.assertIn("client_pid", collection_command)
            self.assertIn("kill -0", collection_command)
            self.assertEqual(runner.collection_timeout, 120)
            for forbidden in ("remote_pwd", "identity_file", "private_key"):
                self.assertNotIn(forbidden, collection_command)

    def test_case_name_rejects_dot_paths(self) -> None:
        for name in (".", ".."):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "non-dot"):
                McProfilerCollectionRequest(
                    target_command="python3 benchmark.py",
                    case_name=name,
                    source_trial_id="source-baseline",
                    source_sha256="a" * 64,
                )

    def test_collection_ignores_unrelated_reports_and_never_overwrites_existing_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = FakeRemoteCollectionRunner(root / "remote")
            unrelated = runner.remote_root / "unrelated" / "case_report" / "report_dumped_result.json"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("{}", encoding="utf-8")
            result = collect_remote_mcprofiler_case(runner, root / "results", self.request())
            self.assertEqual(result.status, "collected")
            self.assertTrue(unrelated.is_file())
            controlled = result.execution_manifest["controlled_remote_directory"]
            self.assertTrue(all(item.startswith(controlled + "/") for item in runner.find_files("report_dumped_result.json", controlled)))

            existing = root / "results-existing" / "mcprofiler_cases" / "mcprof-feed"
            existing.mkdir(parents=True)
            sentinel = existing / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with patch("kernel_opt_agent.profiler.mcprofiler.collector.secrets.token_hex", return_value="feed"):
                blocked = collect_remote_mcprofiler_case(
                    FakeRemoteCollectionRunner(root / "remote-existing"),
                    root / "results-existing",
                    self.request(),
                )
            self.assertEqual((blocked.status, blocked.reason_category), ("failed", "local_case_exists"))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_discovery_hash_ambiguity_and_download_limits_fail_closed(self) -> None:
        class HashFailureRunner(FakeRemoteCollectionRunner):
            def file_sha256(self, relative_path: str) -> str:
                raise OSError("fixture hash failed")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hash_failed = collect_remote_mcprofiler_case(
                HashFailureRunner(root / "hash"), root / "results-hash", self.request()
            )
            self.assertEqual((hash_failed.status, hash_failed.reason_category), ("failed", "discovery_failed"))
            ambiguous = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "ambiguous", "ambiguous"),
                root / "results-ambiguous",
                self.request(),
            )
            self.assertEqual((ambiguous.status, ambiguous.reason_category), ("failed", "ambiguous_reports"))
            oversized = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "oversized"),
                root / "results-oversized",
                self.request(max_artifact_files=1),
            )
            self.assertEqual((oversized.status, oversized.reason_category), ("failed", "artifact_download_failed"))

    def test_changed_report_is_not_imported_as_execution_evidence(self) -> None:
        class ChangedReportRunner(FakeRemoteCollectionRunner):
            def download(self, relative_path, local_path, **kwargs):
                super().download(relative_path, local_path, **kwargs)
                report = next(local_path.rglob("report_dumped_result.json"))
                report.write_text("{}", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            outcome = collect_remote_mcprofiler_case(ChangedReportRunner(root / "remote"), results, self.request())
            self.assertEqual((outcome.status, outcome.reason_category), ("failed", "artifact_hash_mismatch"))
            self.assertFalse((results / "metric_observations.jsonl").exists())

    def test_unsupported_tool_and_missing_report_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing_tool = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "missing-tool", "missing_tool"), root / "results-a", self.request()
            )
            self.assertEqual((missing_tool.status, missing_tool.reason_category), ("unsupported", "tool_unavailable"))
            no_report = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "no-report", "no_report"), root / "results-b", self.request()
            )
            self.assertEqual((no_report.status, no_report.reason_category), ("failed", "report_not_found"))
            self.assertEqual(no_report.fallback, "benchmark_log")

    def test_guard_timeout_and_cancel_are_recorded_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            denied = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "denied"),
                root / "results-denied",
                McProfilerCollectionRequest(
                    target_command="rm -rf /",
                    case_name="denied",
                    source_trial_id="source-baseline",
                    source_sha256="a" * 64,
                ),
            )
            self.assertEqual((denied.status, denied.reason_category), ("failed", "guard_denied"))
            timed_out = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "timeout", "timeout"), root / "results-timeout", self.request()
            )
            self.assertEqual((timed_out.status, timed_out.reason_category), ("failed", "timeout"))
            cancelled = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "cancel", "cancel"), root / "results-cancel", self.request()
            )
            self.assertEqual((cancelled.status, cancelled.reason_category), ("cancelled", "cancelled"))
            for result_dir in ("results-denied", "results-timeout", "results-cancel"):
                rows = [json.loads(line) for line in (root / result_dir / "mcprofiler_collection.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 1)

    def test_runner_without_managed_remote_access_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = collect_remote_mcprofiler_case(object(), Path(temporary), self.request())
            self.assertEqual((result.status, result.reason_category), ("unsupported", "runner_unsupported"))

    def test_failure_message_and_persisted_status_are_redacted(self) -> None:
        secret = "collection-secret-value"

        def redact(value):
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, str):
                return value.replace(secret, "<redacted:secret>")
            return value

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "remote", "failed", secret),
                root / "results",
                self.request(),
                redactor=redact,
                secret_values=(secret,),
            )
            serialized = json.dumps(result.to_dict(redact))
            persisted = (root / "results" / "mcprofiler_collection.jsonl").read_text(encoding="utf-8")
            self.assertEqual(result.status, "failed")
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, persisted)

    def test_main_flow_falls_back_to_benchmark_metrics_when_collection_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trial_dir = root / "trial"
            trial_dir.mkdir()
            kernel = trial_dir / "kernel.py"
            kernel.write_text("print('kernel')\n", encoding="utf-8")
            config = AppConfig.model_validate(
                {
                    "project_name": "mcprofiler-fallback",
                    "execution_mode": "baseline_only",
                    "runner": {"type": "ssh"},
                    "remote": {
                        "host": "example.invalid",
                        "username": "user",
                        "auth_type": "key",
                        "key_path": "unused",
                        "remote_workspace": "/managed/mcprofiler-task",
                    },
                    "kernel": {
                        "sample_path": str(trial_dir),
                        "entry_file": "kernel.py",
                        "correctness_command": "python3 correctness.py",
                        "run_command": "python3 benchmark.py",
                    },
                    "search_space": {},
                    "hardware_detection": {"enabled": False},
                    "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": True},
                }
            )
            runner = FakeRemoteCollectionRunner(root / "remote", "missing_tool")
            db = ExperimentDB(root / "results")
            paths = {
                "trial_dir": trial_dir,
                "kernel": kernel,
                "kernel_copy": kernel,
                "patch": trial_dir / "patch.diff",
            }
            with patch("kernel_opt_agent.main.build_runner", return_value=runner):
                record = run_trial(config, db, "run-id", 0, 0, {}, paths)
            self.assertEqual(record["status"], "benchmark_ok")
            self.assertEqual(record["profiler"]["collection"]["status"], "unsupported")
            self.assertEqual(record["profiler"]["collection"]["reason_category"], "tool_unavailable")
            self.assertEqual(record["profiler"]["result"]["latency_ms"], 1.25)
            self.assertTrue(record["profiler"]["result"]["available_metrics"]["latency_ms"])

            failed_db = ExperimentDB(root / "failed-results")
            with patch("kernel_opt_agent.main.build_runner", return_value=runner), patch(
                "kernel_opt_agent.main.collect_remote_mcprofiler_case", side_effect=RuntimeError("adapter broke")
            ):
                failed_record = run_trial(config, failed_db, "run-id", 0, 0, {}, paths)
            self.assertEqual(failed_record["status"], "benchmark_ok")
            self.assertEqual(failed_record["profiler"]["collection"]["status"], "failed")
            self.assertEqual(failed_record["profiler"]["collection"]["reason_category"], "adapter_exception")
            self.assertEqual(failed_record["profiler"]["result"]["latency_ms"], 1.25)

    def test_downloaded_artifact_with_configured_secret_is_removed(self) -> None:
        secret = "configured-secret-value"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = collect_remote_mcprofiler_case(
                FakeRemoteCollectionRunner(root / "remote", "artifact_secret", secret),
                root / "results",
                self.request(),
                secret_values=(secret,),
            )
            self.assertEqual((result.status, result.reason_category), ("failed", "secret_detected"))
            case_root = root / "results" / "mcprofiler_cases"
            self.assertFalse(case_root.exists() and any(case_root.iterdir()))
            serialized = (root / "results" / "mcprofiler_collection.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(secret, serialized)

    def test_ssh_runner_cancellation_stops_managed_remote_child(self) -> None:
        class Channel:
            def __init__(self, ready=False, returncode=0):
                self.ready = ready
                self.returncode = returncode
                self.closed = False

            def exit_status_ready(self):
                return self.ready

            def recv_ready(self):
                return False

            def recv_stderr_ready(self):
                return False

            def recv_exit_status(self):
                return self.returncode

            def close(self):
                self.closed = True
                self.ready = True

        class Stream:
            def __init__(self, channel):
                self.channel = channel

        class Client:
            def __init__(self):
                self.commands = []
                self.main_channel = Channel()

            def exec_command(self, command, timeout):
                self.commands.append(command)
                if len(self.commands) == 1:
                    stream = Stream(self.main_channel)
                    return None, stream, stream
                self.main_channel.ready = True
                stop = Stream(Channel(ready=True))
                return None, stop, stop

        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "unused", None, "/managed/task workspace"),
            timeout_seconds=5,
        )
        client = Client()
        runner.client = client
        result = runner.run_cancellable("mcprofiler collection", "python3 benchmark.py", should_cancel=lambda: True)
        self.assertEqual(result.returncode, 130)
        self.assertEqual(result.error_message, "command cancelled")
        self.assertEqual(len(client.commands), 2)
        self.assertIn("kill -TERM --", client.commands[1])
        self.assertIn('kill -0 --', client.commands[1])
        self.assertIn("KERNEL_AGENT_PROCESS_TOKEN", client.commands[1])
        self.assertIn("task workspace", client.commands[1])

    def test_ssh_download_rejects_symlinks_windows_paths_and_size_overflow(self) -> None:
        class Attr:
            def __init__(self, filename: str, mode: int, size: int = 0):
                self.filename = filename
                self.st_mode = mode
                self.st_size = size

        class SFTP:
            def __init__(self, mode: str):
                self.mode = mode

            def lstat(self, path: str):
                if self.mode == "symlink" and path.endswith("/link"):
                    return Attr("link", stat.S_IFLNK)
                return Attr(path.rsplit("/", 1)[-1], stat.S_IFDIR)

            def listdir_attr(self, path: str):
                if self.mode == "windows_name":
                    return [Attr("C:\\escape.txt", stat.S_IFREG, 1)]
                if self.mode == "fifo":
                    return [Attr("pipe", stat.S_IFIFO)]
                return [Attr("large.bin", stat.S_IFREG, 10)]

        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "unused", None, "/managed/task"),
            timeout_seconds=5,
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "case"
            runner.sftp = SFTP("symlink")
            with self.assertRaisesRegex(ValueError, "symlink component"):
                runner.download("link/case", target)
            runner.sftp = SFTP("windows_name")
            with self.assertRaisesRegex(ValueError, "unsafe remote artifact name"):
                runner.download("case", target)
            runner.sftp = SFTP("large")
            with self.assertRaisesRegex(ValueError, "exceeds 5 bytes"):
                runner.download("case", target, max_bytes=5)
            runner.sftp = SFTP("fifo")
            with self.assertRaisesRegex(ValueError, "non-regular"):
                runner.download("case", target)

    def test_ssh_timeout_preserves_timeout_when_process_stop_is_unverified(self) -> None:
        class Channel:
            def __init__(self, returncode=0):
                self.ready = False
                self.returncode = returncode

            def exit_status_ready(self):
                return self.ready

            def recv_ready(self):
                return False

            def recv_stderr_ready(self):
                return False

            def recv_exit_status(self):
                return self.returncode

            def close(self):
                self.ready = True

        class Stream:
            def __init__(self, channel):
                self.channel = channel

        class Client:
            def __init__(self):
                self.main = Channel()

            def exec_command(self, command, timeout):
                if "kill -TERM --" in command and "/proc/$pid/environ" in command:
                    self.main.ready = True
                    failed = Channel(returncode=1)
                    failed.ready = True
                    return None, Stream(failed), Stream(failed)
                return None, Stream(self.main), Stream(self.main)

        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "unused", None, "/managed/task"),
            timeout_seconds=0.01,
        )
        runner.client = Client()
        result = runner.run_cancellable("timeout", "python3 benchmark.py")
        self.assertTrue(result.timeout)
        self.assertEqual(result.returncode, 124)
        self.assertIn("could not be verified", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
