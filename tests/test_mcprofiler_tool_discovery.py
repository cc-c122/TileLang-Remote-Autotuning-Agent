from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from kernel_opt_agent.profiler.mcprofiler.tool_discovery import (
    McProfilerCapability, _probe_tools, discover_mcprofiler, persist_capability, validate_tool_path,
)
from kernel_opt_agent.runner.local_runner import CommandResult
from kernel_opt_agent.server.models import SettingsPayload, TaskCreateRequest
from kernel_opt_agent.server.run_request_builder import build_effective_config
from kernel_opt_agent.server.settings_store import SettingsStore


class ProbeFilesystem:
    def __init__(self, files, path_client=None, path_server=None, symlinks=None):
        self.files = set(files)
        self.directories = {str(parent) for name in files for parent in PurePosixPath(name).parents}
        self.symlinks = symlinks or {}
        self.scanned = []
        self.created = []
        self.path_client = path_client
        self.path_server = path_server

    def run(self, **options):
        filesystem = self

        class ProbePath(PurePosixPath):
            @classmethod
            def cwd(cls):
                return cls("/managed")

            def resolve(self):
                return ProbePath(filesystem.symlinks.get(str(self), str(self)))

            def is_file(self):
                return str(self) in filesystem.files

            def is_dir(self):
                return str(self) in filesystem.directories

            def mkdir(self, **kwargs):
                filesystem.created.append(str(self))

        def entries(path):
            filesystem.scanned.append(str(path))
            names = sorted(name for name in filesystem.directories if str(PurePosixPath(name).parent) == str(path))
            return nullcontext(iter(SimpleNamespace(name=PurePosixPath(name).name, path=name,
                                                   is_dir=lambda **kwargs: True) for name in names))

        payload = {"executable": "mcProfiler", "server_executable": "profiler_server", "roots": ["/opt/metax"], "timeout_seconds": 20,
                   **options}
        with patch("pathlib.Path", ProbePath), patch("os.access", return_value=True), patch("os.scandir", side_effect=entries), patch(
            "shutil.which", side_effect=lambda name: self.path_client if name == "mcProfiler" else self.path_server,
        ), patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="version:3.9.0.29\n")) as version:
            result = _probe_tools(payload)
        return result, version


class DiscoveryRunner:
    info = SimpleNamespace(remote_workspace="/managed/task")
    denied_commands = []

    def __init__(self, mode="available"):
        self.mode = mode
        self.commands = []

    def run_cancellable(self, name, command, should_cancel, timeout_seconds):
        self.commands.append((name, command, timeout_seconds))
        if self.mode == "exception":
            raise RuntimeError("fixture transport failure")
        code = {"timeout": 124, "cancelled": 130, "guard_denied": 126, "cleanup_unverified": 1}.get(self.mode, 0)
        capability = McProfilerCapability(available=True, status="available", version="3.9.0.29", discovery_source="path",
                                         executable="/opt/metax/mcProfiler", server_executable="/opt/metax/profiler_server", reason_category="none")
        stdout = "MCPROFILER_DISCOVERY_RESULT " + capability.model_dump_json()
        if self.mode == "invalid":
            stdout = "MCPROFILER_DISCOVERY_RESULT {}"
        error = "remote process-group stop could not be verified" if self.mode == "cleanup_unverified" else None
        return CommandResult(name, command, code, stdout, "", 0, 1, 1,
                             timeout=self.mode == "timeout", guard_denied=self.mode == "guard_denied", error_message=error)


class ToolDiscoveryTests(unittest.TestCase):
    def test_explicit_override_precedes_path_and_install_roots(self):
        fs = ProbeFilesystem(["/custom/mcProfiler", "/custom/profiler_server", "/path/mcProfiler", "/path/profiler_server"],
                             path_client="/path/mcProfiler")
        result, run = fs.run(executable="/custom/mcProfiler")
        self.assertTrue(result["available"])
        self.assertEqual(result["discovery_source"], "explicit")
        self.assertEqual(run.call_args.args[0], ["/custom/mcProfiler", "version"])
        self.assertIsNone(result["server_version"])

    def test_path_precedes_bounded_allowlist(self):
        fs = ProbeFilesystem(["/path/mcProfiler", "/path/profiler_server"], path_client="/path/mcProfiler")
        result, _ = fs.run()
        self.assertEqual(result["discovery_source"], "path")
        self.assertEqual(fs.scanned, [])

    def test_versioned_bundle_is_found_without_disk_scan_or_install(self):
        root = "/opt/metax/mcprofiler_3_9/mcProfiler-ubuntu18"
        fs = ProbeFilesystem([root + "/mcProfiler", root + "/profiler_server"])
        result, version = fs.run()
        self.assertTrue(result["available"])
        self.assertEqual(result["discovery_source"], "allowlist")
        self.assertLessEqual(len(fs.scanned), 32)
        self.assertTrue(all(path.startswith("/opt/metax") for path in fs.scanned))
        self.assertTrue(all(path.startswith("/managed/") for path in fs.created))
        self.assertEqual(version.call_args.args[0][1:], ["version"])

    def test_missing_client_or_required_server_is_not_available(self):
        for files, expected in (([], "client_unavailable"), (["/opt/metax/mcProfiler"], "server_unavailable")):
            with self.subTest(expected=expected):
                result, version = ProbeFilesystem(files).run()
                self.assertFalse(result["available"])
                self.assertEqual(result["reason_category"], expected)
                version.assert_not_called()

    def test_missing_explicit_tool_can_fall_back_with_warning(self):
        fs = ProbeFilesystem(["/path/mcProfiler", "/path/profiler_server"], path_client="/path/mcProfiler")
        result, _ = fs.run(executable="/missing/mcProfiler")
        self.assertEqual(result["discovery_source"], "path")
        self.assertTrue(any("explicit" in value for value in result["warnings"]))

    def test_symlink_to_unrelated_executable_is_not_run(self):
        fs = ProbeFilesystem(["/bin/sh"], path_client="/path/mcProfiler", symlinks={"/path/mcProfiler": "/bin/sh"})
        result, version = fs.run()
        self.assertFalse(result["available"])
        version.assert_not_called()

    def test_version_environment_contains_no_credentials(self):
        with patch.dict("os.environ", {"SSH_PASSWORD": "probe-password-sentinel", "OPENAI_API_KEY": "probe-key-sentinel"}):
            _, version = ProbeFilesystem(["/path/mcProfiler", "/path/profiler_server"], path_client="/path/mcProfiler").run()
        self.assertNotIn("probe-password-sentinel", str(version.call_args.kwargs["env"]))
        self.assertNotIn("probe-key-sentinel", str(version.call_args.kwargs["env"]))

    def test_dangerous_paths_are_rejected_before_any_runner_command(self):
        for value in ("/etc/mcProfiler", "/home/a/.ssh/mcProfiler", "../mcProfiler", "/opt/../bin/mcProfiler",
                      "/bin/sh", "/opt/mcProfiler; id", "/opt/$(id)/mcProfiler", "C:/mcProfiler", "/opt/x\n/mcProfiler"):
            with self.subTest(value=value):
                runner = DiscoveryRunner()
                self.assertEqual(discover_mcprofiler(runner, value).reason_category, "unsafe_tool_path")
                self.assertEqual(runner.commands, [])
                with self.assertRaises(ValueError):
                    validate_tool_path(value, "mcProfiler")

    def test_timeout_cancel_guard_exception_and_unverified_cleanup(self):
        for mode, reason in (("timeout", "timeout"), ("cancelled", "cancelled"), ("guard_denied", "guard_denied"),
                             ("exception", "runner_exception"), ("cleanup_unverified", "cleanup_unverified")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                runner = DiscoveryRunner(mode)
                callbacks = iter([False, mode == "cancelled"])
                result = discover_mcprofiler(runner, results_dir=Path(temporary), should_cancel=lambda: next(callbacks, mode == "cancelled"))
                self.assertFalse(result.available)
                self.assertEqual(result.reason_category, reason)
                self.assertLessEqual(runner.commands[0][2], 20)

    def test_cancel_before_query_runs_no_command(self):
        runner = DiscoveryRunner()
        result = discover_mcprofiler(runner, should_cancel=lambda: True)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(runner.commands, [])

    def test_incomplete_query_output_is_not_claimed_available(self):
        self.assertEqual(discover_mcprofiler(DiscoveryRunner("invalid")).reason_category, "invalid_discovery_result")


class ToolDiscoverySettingsApiTests(unittest.TestCase):
    def test_worker_passes_discovered_paths_to_normal_run_and_effective_config(self):
        from kernel_opt_agent.server.job_worker import JobWorker
        from kernel_opt_agent.server.task_manager import TaskManager

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TaskManager(root / "tasks")
            request = TaskCreateRequest.model_validate({
                "runner": {"type": "ssh"},
                "sample": {"source_type": "inline", "inline_text": "print('fixture')", "entry_file": "kernel.py"},
                "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": True},
            })
            task = manager.create(request)
            context = SimpleNamespace(operation=nullcontext, prepare_task=Mock(), apply_settings=Mock(), secret_values=lambda: [],
                                      detection_runner=lambda: DiscoveryRunner(), redact=lambda value: value,
                                      verify_bound_device=Mock(), release=Mock())
            capability = McProfilerCapability(available=True, status="available", version="3.9.0.29", reason_category="none",
                                             executable="/discovered/mcProfiler", server_executable="/discovered/profiler_server", discovery_source="allowlist")
            with patch("kernel_opt_agent.server.job_worker.discover_mcprofiler", return_value=capability), patch(
                "kernel_opt_agent.server.job_worker.agent_main.run",
            ) as run:
                JobWorker(manager)._run_task(task.task_id, request, context)
            self.assertEqual(manager.get(task.task_id).status, "completed")
            config = run.call_args.args[0]
            self.assertEqual(config.profiler.mcprofiler_executable, capability.executable)
            self.assertEqual(config.profiler._tool_discovery["discovery_source"], "allowlist")
            text = (Path(task.workspace) / "effective_config.yaml").read_text()
            self.assertIn("/discovered/mcProfiler", text)
            self.assertNotIn("_tool_discovery", text)

    def test_saved_override_reaches_effective_config_and_task_override_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "settings.yaml"
            store = SettingsStore(path)
            store.save(SettingsPayload.model_validate({"ssh": {"host": "fixture.invalid", "username": "fixture"},
                                                       "profiler": {"mcprofiler_executable": "/saved/mcProfiler"}}))
            store.save(SettingsPayload.model_validate({"ssh": {"host": "fixture.invalid", "username": "fixture"}}))
            self.assertEqual(store.load_raw().profiler.mcprofiler_executable, "/saved/mcProfiler")
            raw = {"runner": {"type": "ssh"}, "sample": {"source_type": "inline", "inline_text": "print('fixture')", "entry_file": "kernel.py"},
                   "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": True}}
            request = TaskCreateRequest.model_validate(raw)
            cfg = build_effective_config(request, root / "task1", path)
            self.assertEqual(cfg.profiler.mcprofiler_executable, "/saved/mcProfiler")
            raw["profiler"]["mcprofiler_executable"] = "/task/mcProfiler"
            cfg = build_effective_config(TaskCreateRequest.model_validate(raw), root / "task2", path)
            self.assertEqual(cfg.profiler.mcprofiler_executable, "/task/mcProfiler")

    def test_settings_api_accepts_nonsecret_paths_and_rejects_unsafe_values_without_echo(self):
        import kernel_opt_agent.server.app as server

        with tempfile.TemporaryDirectory() as temporary, patch.object(server, "settings_store", SettingsStore(Path(temporary) / "settings.yaml")):
            client = TestClient(server.app)
            response = client.post("/api/settings", json={"profiler": {"mcprofiler_executable": "/opt/metax/mcProfiler"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/settings").json()["settings"]["profiler"]["mcprofiler_executable"], "/opt/metax/mcProfiler")
            secret = "/etc/secret-path-sentinel/mcProfiler"
            response = client.post("/api/settings", json={"profiler": {"mcprofiler_executable": secret}})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(secret, response.text)

    def test_hardware_and_results_expose_tool_availability_separately_from_metrics(self):
        import kernel_opt_agent.server.app as server

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capability = McProfilerCapability(available=True, status="available", version="3.9.0.29", reason_category="none",
                                             executable="/path/mcProfiler", server_executable="/path/profiler_server", discovery_source="path")
            lease = SimpleNamespace(operation=nullcontext, add_redaction_values=Mock(), release=Mock(),
                                    redact=lambda value: value, detection_runner=lambda: DiscoveryRunner(),
                                    resolve=lambda *args: {"hardware": {}, "detection_id": "detection_fixture"})
            store = SimpleNamespace(evidence_root=root, acquire=lambda session_id: lease)
            with patch.object(server, "ssh_sessions", store), patch.object(server, "discover_mcprofiler", return_value=capability):
                response = TestClient(server.app).post("/api/hardware/resolve", json={"ssh_session_id": "fixture-session"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["profiler_capabilities"]["mcprofiler"]["available"])
            persist_capability(root, capability)
            payload = server._result_payload(root)
            self.assertTrue(payload["profiler_capabilities"]["mcprofiler"]["available"])
            self.assertFalse(payload["profiler_available"])


if __name__ == "__main__":
    unittest.main()
