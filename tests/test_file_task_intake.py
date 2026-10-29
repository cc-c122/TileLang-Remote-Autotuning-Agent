"""No-GPU integration: real local Python execution, explicitly synthetic timings."""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from fastapi.testclient import TestClient

from kernel_opt_agent.config_model import TestFilesConfig as FilesConfig
from kernel_opt_agent.file_task import automatic_commands, strict_benchmark, strict_correctness
from kernel_opt_agent.runner.local_runner import CommandResult, LocalRunner
from kernel_opt_agent.server.job_worker import JobWorker
from kernel_opt_agent.server.models import TaskCreateRequest
from kernel_opt_agent.server.run_request_builder import build_effective_config
from kernel_opt_agent.server.sample_uploads import SampleUploadStore
from kernel_opt_agent.server.settings_store import SettingsStore
from kernel_opt_agent.server.ssh_sessions import SessionError
from kernel_opt_agent.server.task_manager import TaskManager
from tests.test_source_optimizer import _source


server = importlib.import_module("kernel_opt_agent.server.app")


class FileTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sample = self.root / "sample"
        self.manager = TaskManager(self.root / "tasks")
        self.uploads = SampleUploadStore(self.root / "uploads")
        settings = SettingsStore(self.root / "settings.yaml")
        self.worker = JobWorker(self.manager, settings.path, self.uploads)
        self.patches = [patch.dict("os.environ", {"OPENAI_API_KEY": ""})] + [patch.object(server, key, value) for key, value in {
            "manager": self.manager, "worker": self.worker, "upload_store": self.uploads, "settings_store": settings,
        }.items()]
        for context in self.patches:
            context.start()
        self.client = TestClient(server.app)

    def tearDown(self):
        for task in self.manager.list_recent():
            if task.status not in {"completed", "failed", "cancelled"}:
                self.manager.request_cancel(task.task_id)
                self.wait(task.task_id)
        self.client.close()
        for context in reversed(self.patches):
            context.stop()
        import logging
        for handler in list(logging.getLogger().handlers):
            if str(self.root) in str(getattr(handler, "baseFilename", "")):
                logging.getLogger().removeHandler(handler)
                handler.close()
        self.temporary.cleanup()

    def fixture(self, mode="normal"):
        files = {
            "ops/kernel.py": _source(),
            "tilelang/__init__.py": "# Test-only DSL, not a GPU runtime.\n",
            "tilelang/language.py": '''# Synthetic DSL semantics for no-GPU integration only.
def prim_func(fn): return fn
def alloc_fragment(shape, dtype): return [0] * shape[0]
def Parallel(size): return range(size)
def copy(source, target): target[:] = [value + OFFSET for value in source]
'''.replace("OFFSET", "1" if mode == "correctness_failed" else "0"),
            "reference.py": "def reference(values): return list(values)\n",
            "checks/correctness check.py": '''from ops.kernel import factory
from reference import reference
values = list(range(8))
output = [0] * 8
factory()(values, output)
error = max(abs(a-b) for a,b in zip(output, reference(values)))
status = 'PASS' if error == 0 else 'FAIL'
print(f'CORRECTNESS_RESULT status={status} max_error={error} reason="fixed reference and tolerance=0"')
raise SystemExit(0 if error == 0 else 1)
''',
            "checks/benchmark check.py": '''from pathlib import Path
from ops.kernel import factory
output = [0] * 8
factory()(list(range(8)), output)
changed = 'TL.copy(' in Path('ops/kernel.py').read_text()
print('TEST_DATA: synthetic latency fixture, not GPU performance')
latency = CANDIDATE if changed else 10
MUTATION
print(f'BENCHMARK_RESULT latency_ms={latency}')
'''.replace("CANDIDATE", "12" if mode == "slower" else "5").replace("MUTATION", "if changed: Path('reference.py').write_text('tampered')" if mode == "mutated_test" else ""),
        }
        for name, source in files.items():
            path = self.sample / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return files

    def request(self, files=None):
        files = files or self.fixture()
        response = self.client.post("/api/samples/upload", files=[("files", (name, content.encode(), "text/plain")) for name, content in files.items()], data={"entry_file": "ops/kernel.py"})
        self.assertEqual(response.status_code, 200, response.text)
        return {
            "project_name": "TEST_DATA-file-intake-no-GPU",
            "sample": {"source_type": "upload", "upload_id": response.json()["upload_id"], "entry_file": "ops/kernel.py"},
            "test_files": {"correctness": "checks/correctness check.py", "benchmark": "checks/benchmark check.py"},
            "runner": {"type": "local"}, "profiler": {"enabled": False},
            "budget": {"timeout_seconds": 10},
            "optimization": {"enabled": True, "max_candidates": 1, "benchmark_repeats": 3},
        }

    def wait(self, task_id):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            task = self.manager.get(task_id)
            if task.status in {"completed", "failed", "cancelled"}:
                return task
            time.sleep(0.02)
        self.fail("test task did not finish")

    def run_request(self, payload):
        response = self.client.post("/api/tasks", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        task = self.wait(response.json()["task_id"])
        result = self.client.get(f"/api/tasks/{task.task_id}/results").json()["results"]
        return task, result

    def test_uploaded_files_run_existing_source_loop_and_accept_synthetic_gain(self):
        task, result = self.run_request(self.request())
        self.assertEqual(task.status, "completed", task.error)
        self.assertEqual(task.execution_mode, "source_optimization")
        self.assertEqual(result["preflight"]["status"], "passed")
        self.assertEqual(result["preflight"]["gpu_check"], "local_demo_no_gpu_validation")
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(trial["status"], "accepted")
        self.assertTrue(trial["artifacts"]["rollback"]["verified"])
        self.assertEqual(len(trial["candidate_latency_ms"]), 3)
        self.assertTrue(trial["artifacts"]["performance_gate"]["baseline_recheck_passed"])
        self.assertTrue(result["file_task"]["entry_execution_attestation"])
        self.assertIn("reference.py", result["file_task"]["protected_files"])
        for name in ("run_request.yaml", "effective_config.yaml"):
            self.assertTrue((Path(task.workspace) / name).is_file())
        events = [event["type"] for event in self.manager.get(task.task_id).events]
        self.assertLess(events.index("preflight_completed"), events.index("build_started"))
        self.assertLess(events.index("correctness_started"), events.index("benchmark_started"))

    def test_missing_file_rejected_without_task_workspace(self):
        request = self.request()
        request["test_files"]["benchmark"] = "absent.py"
        response = self.client.post("/api/tasks", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertIn("sample file not found", response.text)
        self.assertEqual(list(self.manager.tasks_root.iterdir()), [])

    def test_traversal_and_shell_paths_are_rejected(self):
        request = self.request()
        for path in ("../check.py", "/check.py", "C:/check.py", "sub\\check.py", "x;echo.py", "x$HOME.py", "x%PATH%.py", "x&run.py"):
            with self.subTest(path=path):
                request["test_files"]["correctness"] = path
                self.assertEqual(self.client.post("/api/tasks", json=request).status_code, 422)
        self.assertEqual(list(self.manager.tasks_root.iterdir()), [])

    def test_missing_dependency_fails_preflight_without_executing_tests(self):
        files = self.fixture()
        files["ops/kernel.py"] = "import missing_runtime_for_file_task_fixture\n" + files["ops/kernel.py"].replace("from __future__ import annotations\n", "")
        task, result = self.run_request(self.request(files))
        self.assertEqual(task.status, "failed")
        self.assertIn("missing_runtime_for_file_task_fixture", task.error)
        self.assertEqual(result["preflight"]["error_category"], "dependency_missing")
        self.assertNotIn("build_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_printed_pass_without_protocol_is_incompatible(self):
        files = self.fixture()
        files["checks/correctness check.py"] = "from ops.kernel import factory\nprint('PASS')\n"
        task, result = self.run_request(self.request(files))
        self.assertEqual(task.status, "failed")
        self.assertIn("script_protocol_incompatible", task.error)
        self.assertFalse(result["summary_table"][0]["correctness"]["passed"])
        self.assertNotIn("benchmark_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_structured_pass_without_loading_candidate_is_rejected(self):
        files = self.fixture()
        files["checks/correctness check.py"] = "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=\"not a real check\"')\n"
        task, result = self.run_request(self.request(files))
        self.assertEqual(task.status, "failed")
        self.assertIsNone(result["best_kernel"])
        self.assertEqual(result["summary_table"][0]["status"], "script_protocol_incompatible")

    def test_structured_fail_exit_one_keeps_error_and_reason(self):
        files = self.fixture()
        files["checks/correctness check.py"] = files["checks/correctness check.py"].replace(
            "error = max(abs(a-b) for a,b in zip(output, reference(values)))", "error = 0.125",
        )
        request = self.request(files)
        request["optimization"]["enabled"] = False
        task, result = self.run_request(request)
        self.assertEqual(task.status, "failed")
        self.assertEqual(result["summary_table"][0]["status"], "correctness_failed")
        trial = result["failed_cases"][0]
        self.assertEqual(trial["status"], "correctness_failed")
        self.assertEqual(trial["error"]["category"], "correctness_failed")
        self.assertFalse(trial["correctness"]["passed"])
        self.assertFalse(trial["correctness"]["parse_error"])
        self.assertEqual(trial["correctness"]["max_error"], 0.125)
        self.assertEqual(trial["correctness"]["reason"], "fixed reference and tolerance=0")
        self.assertTrue(trial["execution_validation"]["rollback"]["verified"])
        self.assertNotIn("benchmark_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_correctness_timeout_keeps_category_stage_and_partial_logs(self):
        actual_run = LocalRunner.run

        def timed_out(runner, name, command):
            if name == "correctness":
                return CommandResult(name, command, 124, "partial correctness output", "partial correctness error", 0, 1, 1,
                                     timeout=True, error_message="command timed out after 10s")
            return actual_run(runner, name, command)

        request = self.request()
        request["optimization"]["enabled"] = False
        with patch.object(LocalRunner, "run", timed_out):
            task, result = self.run_request(request)
        self.assertEqual(task.status, "failed")
        self.assertIn("timed out after 10s", task.error)
        self.assertEqual(result["summary_table"][0]["status"], "correctness_timeout")
        trial = result["failed_cases"][0]
        self.assertEqual(trial["status"], "correctness_timeout")
        self.assertEqual(trial["error"]["category"], "correctness_timeout")
        self.assertEqual(trial["error"]["stage"], "correctness")
        self.assertIn("timed out after 10s", trial["error"]["message"])
        self.assertFalse(trial["correctness"]["passed"])
        self.assertFalse(trial["correctness"]["parse_error"])
        self.assertTrue(trial["execution_validation"]["rollback"]["verified"])
        self.assertIn("partial correctness output", Path(trial["paths"]["stdout"]).read_text(encoding="utf-8"))
        self.assertIn("partial correctness error", Path(trial["paths"]["stderr"]).read_text(encoding="utf-8"))
        self.assertNotIn("benchmark_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_correctness_timeout_cannot_be_overridden_by_complete_pass_record(self):
        actual_run = LocalRunner.run

        def timed_out(runner, name, command):
            if name == "correctness":
                digest = hashlib.sha256((runner.workspace / "ops/kernel.py").read_bytes()).hexdigest()
                output = 'CORRECTNESS_RESULT status=PASS max_error=0 reason="before hanging"\nFILE_TASK_ENTRY_SHA256=' + digest
                return CommandResult(name, command, 124, output, "", 0, 1, 1, timeout=True)
            return actual_run(runner, name, command)

        request = self.request()
        request["optimization"]["enabled"] = False
        with patch.object(LocalRunner, "run", timed_out), patch("kernel_opt_agent.main.strict_correctness") as parse:
            task, result = self.run_request(request)
        parse.assert_not_called()
        self.assertEqual(task.status, "failed")
        self.assertEqual(result["summary_table"][0]["status"], "correctness_timeout")
        trial = result["failed_cases"][0]
        self.assertEqual(trial["status"], "correctness_timeout")
        self.assertEqual(trial["error"]["stage"], "correctness")
        self.assertIn("timed out after 10s", trial["error"]["message"])
        self.assertFalse(trial["correctness"]["passed"])
        self.assertIsNone(result["best_row"])
        self.assertNotIn("benchmark_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_missing_benchmark_protocol_fails_instead_of_guessing_latency(self):
        files = self.fixture()
        files["checks/benchmark check.py"] = "from ops.kernel import factory\nprint('latency=1 PASS')\n"
        task, result = self.run_request(self.request(files))
        self.assertEqual(task.status, "failed")
        self.assertIn("script_protocol_incompatible", task.error)
        self.assertIsNone(result["best_kernel"])

    def test_candidate_correctness_failure_rolls_back_and_never_benchmarks(self):
        task, result = self.run_request(self.request(self.fixture("correctness_failed")))
        self.assertEqual(task.status, "completed", task.error)
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(trial["status"], "correctness_failed")
        self.assertEqual(trial["correctness"]["max_error"], 1)
        self.assertEqual(trial["correctness"]["reason"], "fixed reference and tolerance=0")
        self.assertTrue(trial["rollback_verified"])
        self.assertEqual(trial["candidate_latency_ms"], [])
        self.assertNotIn("TL.copy(", result["best_kernel"])

    def test_candidate_correctness_timeout_keeps_reason_and_rolls_back(self):
        actual_run = LocalRunner.run

        def timed_out(runner, name, command):
            if name == "correctness" and runner.workspace.parent.name.startswith("source-"):
                return CommandResult(name, command, 124, "partial candidate output", "", 0, 1, 1,
                                     timeout=True, error_message="command timed out after 10s")
            return actual_run(runner, name, command)

        with patch.object(LocalRunner, "run", timed_out):
            task, result = self.run_request(self.request())
        self.assertEqual(task.status, "completed", task.error)
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(trial["status"], "correctness_failed")
        self.assertIn("correctness_timeout", trial["correctness"]["reason"])
        self.assertIn("timed out after 10s", trial["decision_reason"])
        self.assertNotIn("script_protocol_incompatible", trial["decision_reason"])
        self.assertTrue(trial["rollback_verified"])
        self.assertEqual(trial["candidate_latency_ms"], [])
        self.assertNotIn("TL.copy(", result["best_kernel"])

    def test_slower_candidate_is_recorded_and_baseline_retained(self):
        task, result = self.run_request(self.request(self.fixture("slower")))
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(task.status, "completed", task.error)
        self.assertEqual(trial["status"], "no_improvement")
        self.assertTrue(trial["rollback_verified"])
        self.assertEqual(trial["candidate_latency_ms"], [12, 12, 12])
        self.assertIsNone(result["source_optimization"]["accepted_trial_id"])
        self.assertNotIn("TL.copy(", result["best_kernel"])

    def test_candidate_cannot_change_reference_or_other_protected_files(self):
        task, result = self.run_request(self.request(self.fixture("mutated_test")))
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(trial["status"], "validation_failed")
        self.assertTrue(trial["rollback_verified"])
        root = Path(trial["artifacts"]["source_after"]).parent / "workspace"
        self.assertEqual(hashlib.sha256((root / "reference.py").read_bytes()).hexdigest(), result["file_task"]["protected_files"]["reference.py"])
        self.assertNotIn("TL.copy(", result["best_kernel"])

    def test_cancel_after_current_command_stops_new_repeats_and_restores_source(self):
        request = self.request()
        ready, release = threading.Event(), threading.Event()
        actual_run = LocalRunner.run
        calls = []

        def controlled_run(runner, name, command):
            is_candidate = runner.workspace.parent.name.startswith("source-")
            if is_candidate:
                calls.append(name)
            if is_candidate and name == "benchmark_1":
                ready.set()
                if not release.wait(20):
                    raise TimeoutError("test did not release controlled command")
            return actual_run(runner, name, command)

        with patch.object(LocalRunner, "run", controlled_run):
            response = self.client.post("/api/tasks", json=request)
            task_id = response.json()["task_id"]
            try:
                self.assertTrue(ready.wait(30))
                self.assertEqual(self.client.post(f"/api/tasks/{task_id}/cancel").status_code, 200)
            finally:
                release.set()
            task = self.wait(task_id)
        result = self.client.get(f"/api/tasks/{task_id}/results").json()["results"]
        self.assertEqual(task.status, "cancelled", task.error)
        trial = result["source_optimization"]["trials"][0]
        self.assertEqual(trial["status"], "cancelled")
        self.assertTrue(trial["rollback_verified"])
        self.assertNotIn("benchmark_2", calls)
        self.assertEqual(result["source_optimization"]["accepted_trial_id"], None)
        self.assertIn("task_cancelled", [event["type"] for event in self.manager.get(task_id).events])

    def test_unsupported_source_is_explicitly_baseline_only(self):
        files = self.fixture()
        files["ops/kernel.py"] = "def factory():\n    def copy(a, b): b[:] = a\n    return copy\n"
        task, result = self.run_request(self.request(files))
        self.assertEqual(task.status, "completed", task.error)
        self.assertEqual(task.execution_mode, "baseline_only")
        self.assertEqual(result["source_optimization"]["status"], "unavailable")
        self.assertTrue(result["source_optimization"]["reason"])
        self.assertIsNone(result["improvement_percent"])
        self.assertNotIn("source_optimization_started", [event["type"] for event in self.manager.get(task.task_id).events])

    def test_explicit_commands_remain_advanced_and_file_hashes_are_preserved(self):
        self.fixture()
        request = TaskCreateRequest.model_validate({
            "sample": {"source_type": "path", "path": str(self.sample), "entry_file": "ops/kernel.py"},
            "test_files": {"correctness": "checks/correctness check.py", "benchmark": "checks/benchmark check.py"},
            "commands": {"build_command": "custom-build", "correctness_command": "custom-check", "benchmark_command": "custom-benchmark"},
        })
        config = build_effective_config(request, self.root / "advanced-task")
        self.assertEqual(config.kernel.correctness_command, "custom-check")
        self.assertFalse(config.file_task.attest_entry)
        self.assertIn("reference.py", config.file_task.protected_files)

    def test_ssh_file_mode_missing_password_env_fails_before_workspace(self):
        request = self.request()
        request["runner"]["type"] = "ssh"
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.post("/api/tasks", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertIn("ssh_password_env_not_set", response.text)
        self.assertEqual(list(self.manager.tasks_root.iterdir()), [])

    def test_session_multi_card_and_device_change_fail_closed(self):
        for code in ("device_selection_required", "selected_device_changed", "gpu_detection_failed"):
            with self.subTest(code=code):
                request = self.request()
                request.update(runner={"type": "ssh"}, ssh_session_id="test-session-not-a-real-credential")
                lease = MagicMock()
                lease.secret_values.return_value = []
                lease.select.side_effect = SessionError(code)
                with patch.object(server.ssh_sessions, "acquire", return_value=lease):
                    response = self.client.post("/api/tasks", json=request)
                self.assertEqual(response.status_code, 409)
                self.assertIn(code, response.text)
                lease.release.assert_called_once()
        self.assertEqual(list(self.manager.tasks_root.iterdir()), [])

    def test_saved_settings_single_device_uses_scoped_memory_lease(self):
        request = self.request()
        request["runner"]["type"] = "ssh"
        lease = MagicMock()
        lease.secret_values.return_value = []
        with patch.dict("os.environ", {"KERNEL_AGENT_SSH_PASSWORD": "test-only-password-not-a-real-credential"}), \
             patch.object(server.ssh_sessions, "create", return_value={"session_id": "test-only-internal-session"}) as create, \
             patch.object(server.ssh_sessions, "acquire", return_value=lease), \
             patch.object(server.ssh_sessions, "delete") as delete, \
             patch.object(self.worker, "submit") as submit:
            response = self.client.post("/api/tasks", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("test-only-password-not-a-real-credential", response.text)
            self.assertNotIn("test-only-internal-session", response.text)
            self.assertEqual(create.call_args.args[0].password.get_secret_value(), "test-only-password-not-a-real-credential")
            lease.select.assert_called_once_with(None, {})
            delete.assert_called_once_with("test-only-internal-session")
            self.assertIs(submit.call_args.kwargs["execution_context"], lease)
            self.manager.set_status(response.json()["task_id"], "cancelled")
            lease.release()

    def test_known_environment_secrets_are_not_in_results_or_logs(self):
        files = self.fixture()
        files["checks/correctness check.py"] = "import os\nsecret = os.environ['OPENAI_API_KEY']\n" + files["checks/correctness check.py"].replace('reason="fixed reference and tolerance=0"', 'reason="{secret}"')
        request = self.request(files)
        request["optimization"]["enabled"] = False
        secrets = {"OPENAI_API_KEY": "FAKE_API_SECRET_FOR_FILE_TEST", "KERNEL_AGENT_SSH_PASSWORD": "FAKE_PASSWORD_FOR_FILE_TEST"}
        with patch.dict("os.environ", secrets):
            task, result = self.run_request(request)
        self.assertEqual(task.status, "completed", task.error)
        serialized = json.dumps(result)
        for value in secrets.values():
            self.assertNotIn(value, serialized)
            for path in Path(task.workspace).rglob("*"):
                if path.is_file():
                    self.assertNotIn(value.encode(), path.read_bytes(), path.name)


class FileProtocolTests(unittest.TestCase):
    def test_nonzero_exit_preserves_binding_but_never_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "kernel.py"
            entry.write_text("value = 1\n", encoding="utf-8")
            digest = hashlib.sha256(entry.read_bytes()).hexdigest()
            commands = automatic_commands("local", "kernel.py", FilesConfig(correctness="check.py", benchmark="bench.py"))
            runner = LocalRunner(root, 5)
            for status, load_entry in (("FAIL", True), ("PASS", True), ("FAIL", False)):
                with self.subTest(status=status, load_entry=load_entry):
                    script = "import kernel\n" if load_entry else ""
                    script += f'print(\'CORRECTNESS_RESULT status={status} max_error=0.125 reason="reference mismatch"\')\nraise SystemExit(1)\n'
                    (root / "check.py").write_text(script, encoding="utf-8")
                    command = runner.run("correctness", commands["correctness_command"])
                    self.assertEqual(command.returncode, 1)
                    self.assertEqual("FILE_TASK_ENTRY_SHA256=" in command.stdout, load_entry)
                    parsed = strict_correctness(command.stdout, command.stderr, command.returncode, digest)
                    self.assertFalse(parsed.passed)
                    self.assertEqual(parsed.parse_error, not load_entry)
                    if load_entry:
                        self.assertEqual(parsed.max_error, 0.125)
                        self.assertEqual(parsed.reason, "reference mismatch")

    def test_timeout_partial_logs_remain_text_without_timing_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = LocalRunner(Path(directory), 1)
            with patch("kernel_opt_agent.runner.local_runner.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 1, output=b"partial output", stderr=b"partial error")):
                result = runner.run("preflight", "python -B -c \"print(1)\"")
        self.assertTrue(result.timeout)
        self.assertEqual(result.stdout, "partial output")
        self.assertEqual(result.stderr, "partial error")

    def test_strict_records_do_not_accept_exit_code_only_or_fallbacks(self):
        for output in ("", "PASS", 'CORRECTNESS_RESULT status=PASS max_error=0 reason="x"\nCORRECTNESS_RESULT status=FAIL max_error=1 reason="y"'):
            self.assertFalse(strict_correctness(output, "", 0).passed)
        for output in ("", "PASS latency=1", "BENCHMARK_RESULT latency_ms=0", "BENCHMARK_RESULT latency_ms=nan", "BENCHMARK_RESULT latency_ms=1.2junk"):
            self.assertTrue(strict_benchmark(output).parse_error)
        self.assertEqual(strict_benchmark("BENCHMARK_RESULT latency_ms=1e-3").latency, 0.001)

    def test_file_mode_default_enables_existing_source_optimizer_only(self):
        request = TaskCreateRequest.model_validate({"sample": {"source_type": "path", "path": "sample", "entry_file": "kernel.py"}, "test_files": {"correctness": "correctness.py", "benchmark": "benchmark.py"}})
        self.assertTrue(request.optimization.enabled)
        self.assertFalse(TaskCreateRequest.model_validate({"sample": {"source_type": "inline", "inline_text": "print(1)"}}).optimization.enabled)

    def test_generated_command_is_guarded_and_shell_quotes_spaces(self):
        from kernel_opt_agent.runner.command_guard import validate
        commands = automatic_commands("ssh", "operator file.py", FilesConfig(correctness="correctness check.py", benchmark="benchmark check.py"))
        for command in commands.values():
            self.assertTrue(validate(command, "/tmp/managed/task", "/tmp/managed/task").allowed)
        self.assertIn("'correctness check.py'", commands["correctness_command"])


if __name__ == "__main__":
    unittest.main()
