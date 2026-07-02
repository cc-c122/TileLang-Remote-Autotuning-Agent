from __future__ import annotations

import csv
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config, safe_config_dict
from kernel_opt_agent.main import runner_exception_category
from kernel_opt_agent.agent.optimizer_policy import OptimizerPolicy
from kernel_opt_agent.agent.prompt_templates import build_user_prompt
from kernel_opt_agent.hardware.hardware_info import HardwareInfo
from kernel_opt_agent.kernel.template_manager import TemplateManager
from kernel_opt_agent.runner.command_guard import validate
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner
from kernel_opt_agent.storage.experiment_db import ExperimentDB


ROOT = Path(__file__).resolve().parents[1]


class FakeSFTPAttr:
    def __init__(self, filename: str, st_mode: int = stat.S_IFREG):
        self.filename = filename
        self.st_mode = st_mode


class FakeSFTPFile:
    def __init__(self, files: set[str], path: str):
        self.files = files
        self.path = path

    def __enter__(self):
        self.files.add(self.path)
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def write(self, text: str) -> None:
        self.files.add(self.path)


class FakeSFTP:
    def __init__(self, entries: dict[str, list[FakeSFTPAttr]] | None = None, files: set[str] | None = None):
        self.entries = entries or {}
        self.files = files or set()
        self.removed: list[str] = []

    def listdir_attr(self, remote_path: str):
        return self.entries.get(remote_path, [])

    def stat(self, remote_path: str):
        if remote_path not in self.files:
            raise OSError(remote_path)
        return FakeSFTPAttr(Path(remote_path).name)

    def open(self, remote_path: str, mode: str):
        return FakeSFTPFile(self.files, remote_path)

    def remove(self, remote_path: str):
        self.removed.append(remote_path)
        self.files.discard(remote_path)


class V1HardeningTests(unittest.TestCase):
    def test_command_guard_rejects_dangerous_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            result = validate("rm -rf /", workspace, workspace)
            self.assertFalse(result.allowed)
            self.assertIn("dangerous", result.reason)

    def test_template_placeholder_mismatch_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "kernel.py"
            template.write_text("BM = {{BM}}\nBN = {{BN}}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                TemplateManager(template, {"BM": [16]})

    def test_llm_strategy_falls_back_when_planner_unavailable(self) -> None:
        policy = OptimizerPolicy({"BM": [16, 32], "USE_SHARED": [True, False]}, seed=1, planner=None)
        candidates, source = policy.propose("llm", 2, set(), [])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(source, "llm_rule_based_fallback")
        for candidate in candidates:
            self.assertIn(candidate["BM"], [16, 32])
            self.assertIn(candidate["USE_SHARED"], [True, False])

    def test_optimizer_filters_values_failed_by_safe_probe(self) -> None:
        policy = OptimizerPolicy(
            {"NUM_THREADS": [128, 256], "VECTOR_WIDTH": [1, 4], "BM": [16]},
            seed=1,
            safe_probe_results=[
                {"param_name": "NUM_THREADS", "candidate_value": 256, "status": "timeout"},
                {"param_name": "VECTOR_WIDTH", "candidate_value": 4, "status": "failed"},
            ],
        )
        candidates = policy.grid(4, set())
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(candidate["NUM_THREADS"], 128)
            self.assertEqual(candidate["VECTOR_WIDTH"], 1)

    def test_optimizer_keeps_search_space_when_probe_blocks_all_values(self) -> None:
        policy = OptimizerPolicy(
            {"NUM_THREADS": [128, 256], "BM": [16]},
            seed=1,
            safe_probe_results=[
                {"param_name": "NUM_THREADS", "candidate_value": 128, "status": "failed"},
                {"param_name": "NUM_THREADS", "candidate_value": 256, "status": "failed"},
            ],
        )
        candidates = policy.grid(2, set())
        self.assertEqual({candidate["NUM_THREADS"] for candidate in candidates}, {128, 256})

    def test_optimizer_filters_llm_candidates_failed_by_safe_probe(self) -> None:
        class FakePlanner:
            def propose(self, history, count):
                return [
                    {"NUM_THREADS": 256, "BM": 16},
                    {"NUM_THREADS": 128, "BM": 16},
                ]

        policy = OptimizerPolicy(
            {"NUM_THREADS": [128, 256], "BM": [16]},
            seed=1,
            planner=FakePlanner(),
            safe_probe_results=[{"param_name": "NUM_THREADS", "candidate_value": 256, "status": "failed"}],
        )
        candidates = policy.llm(2, set(), [])
        self.assertEqual(candidates, [{"NUM_THREADS": 128, "BM": 16}])

    def test_llm_prompt_includes_safe_probe_context(self) -> None:
        hardware_info = HardwareInfo.unknown()
        hardware_info.set_field("backend", "unknown", "unknown", "unknown", "not detected")
        prompt = build_user_prompt(
            {"NUM_THREADS": [128, 256]},
            [],
            1,
            [{"param_name": "NUM_THREADS", "candidate_value": 256, "status": "failed", "inference": "failed probe", "confidence": "low"}],
            hardware_info,
        )
        payload = json.loads(prompt)
        self.assertIn("safe_probe_context", payload)
        self.assertIn("hardware_info", payload)
        self.assertEqual(payload["safe_probe_context"]["unavailable_values"][0]["candidate_value"], 256)
        self.assertIn("hardware_assumptions", payload["output_schema"]["candidates"][0])
        self.assertIn("confidence", payload["output_schema"]["candidates"][0])

    def test_conservative_mode_avoids_largest_tile_values(self) -> None:
        policy = OptimizerPolicy(
            {"BM": [16, 32, 64], "BN": [32, 64], "BK": [32, 64], "NUM_THREADS": [128, 256]},
            conservative_mode=True,
        )
        candidates = policy.grid(3, set())
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertNotEqual(candidate["BM"], 64)
            self.assertNotEqual(candidate["BN"], 64)
            self.assertNotEqual(candidate["BK"], 64)

    def test_ssh_remote_workspace_is_shell_quoted(self) -> None:
        runner = SSHRunner(
            SSHConnectionInfo(
                host="example.invalid",
                port=22,
                username="user",
                auth_type="key",
                key_path="~/.ssh/id_rsa",
                password_env=None,
                remote_workspace="/tmp/work space/quote'test",
            ),
            timeout_seconds=5,
        )
        self.assertEqual(runner._build_remote_command("python benchmark.py"), "cd '/tmp/work space/quote'\"'\"'test' && python benchmark.py")

    def test_experiment_db_starts_each_run_from_empty_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            db = ExperimentDB(results)
            db.append(
                {
                    "run_id": "old",
                    "iteration": 0,
                    "candidate_id": 0,
                    "status": "benchmark_ok",
                    "metrics": {},
                    "objective": {},
                    "paths": {},
                }
            )
            self.assertEqual(len((results / "experiments.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            ExperimentDB(results)
            self.assertEqual((results / "experiments.jsonl").read_text(encoding="utf-8"), "")
            self.assertEqual((results / "failed_cases.jsonl").read_text(encoding="utf-8"), "")
            with (results / "summary.csv").open(newline="", encoding="utf-8") as f:
                self.assertEqual(list(csv.DictReader(f)), [])

    def test_password_auth_runner_reads_env_password(self) -> None:
        env_name = "KERNEL_AGENT_TEST_PASSWORD"
        old = os.environ.get(env_name)
        os.environ[env_name] = "secret-password"
        try:
            runner = SSHRunner(
                SSHConnectionInfo("example.invalid", 22, "user", "password", None, env_name, "/tmp/kernel-agent"),
                timeout_seconds=5,
            )
            self.assertEqual(runner._password_from_env(), "secret-password")
        finally:
            if old is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = old

    def test_password_auth_missing_env_fails_clearly(self) -> None:
        env_name = "KERNEL_AGENT_MISSING_PASSWORD"
        os.environ.pop(env_name, None)
        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "password", None, env_name, "/tmp/kernel-agent"),
            timeout_seconds=5,
        )
        with self.assertRaisesRegex(ValueError, "SSH password env var is not set"):
            runner._password_from_env()

    def test_password_auth_missing_password_env_config_fails(self) -> None:
        config = yaml.safe_load((ROOT / "kernel_opt_agent" / "config.example.yaml").read_text(encoding="utf-8"))
        config["runner"]["type"] = "ssh"
        config["remote"]["auth_type"] = "password"
        config["remote"].pop("password_env", None)
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "password_missing_env.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "remote.password_env is required"):
                load_config(str(config_path))

    def test_plaintext_remote_password_is_rejected(self) -> None:
        config = yaml.safe_load((ROOT / "kernel_opt_agent" / "config.example.yaml").read_text(encoding="utf-8"))
        config["runner"]["type"] = "ssh"
        config["remote"]["auth_type"] = "password"
        config["remote"]["password"] = "do-not-store"
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "plaintext_password.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sensitive value field is not allowed"):
                load_config(str(config_path))

    def test_safe_config_does_not_include_password_value(self) -> None:
        env_name = "KERNEL_AGENT_TEST_PASSWORD"
        old = os.environ.get(env_name)
        os.environ[env_name] = "secret-password"
        try:
            config = yaml.safe_load((ROOT / "kernel_opt_agent" / "config.example.yaml").read_text(encoding="utf-8"))
            config["runner"]["type"] = "ssh"
            config["remote"]["auth_type"] = "password"
            config["remote"]["password_env"] = env_name
            config["remote"]["key_path"] = None
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "password.yaml"
                config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
                safe = safe_config_dict(load_config(str(config_path)))
        finally:
            if old is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = old
        dumped = json.dumps(safe)
        self.assertNotIn("secret-password", dumped)
        self.assertEqual(safe["remote"]["password_env"], env_name)

    def test_ssh_runner_exception_is_classified(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.ssh.example.yaml"))
        self.assertEqual(runner_exception_category(config, "timed out"), "ssh_connection_failed")
        self.assertEqual(runner_exception_category(config, "SFTP upload failed"), "sftp_upload_failed")

    def test_safe_config_redacts_ssh_key_path(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.ssh.example.yaml"))
        self.assertEqual(safe_config_dict(config)["remote"]["key_path"], "<redacted:key_path>")

    def test_ssh_workspace_clear_rejects_broad_paths(self) -> None:
        runner = SSHRunner(
            SSHConnectionInfo(
                host="example.invalid",
                port=22,
                username="user",
                auth_type="key",
                key_path="~/.ssh/id_rsa",
                password_env=None,
                remote_workspace="/tmp/kernel-agent",
            ),
            timeout_seconds=5,
        )
        self.assertFalse(runner._is_safe_workspace_to_clear("/"))
        self.assertFalse(runner._is_safe_workspace_to_clear("/tmp"))
        self.assertFalse(runner._is_safe_workspace_to_clear("/usr/local/kernel-agent"))
        self.assertFalse(runner._is_safe_workspace_to_clear("/etc/kernel-agent"))
        self.assertFalse(runner._is_safe_workspace_to_clear("/var/tmp/kernel-agent"))
        self.assertTrue(runner._is_safe_workspace_to_clear("/tmp/kernel-agent"))

    def test_ssh_workspace_marker_initializes_empty_workspace(self) -> None:
        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "~/.ssh/id_rsa", None, "/tmp/kernel-agent"),
            timeout_seconds=5,
        )
        fake = FakeSFTP(entries={"/tmp/kernel-agent": []})
        runner.sftp = fake
        runner._ensure_workspace_marker("/tmp/kernel-agent")
        self.assertIn("/tmp/kernel-agent/.kernel_opt_agent_workspace", fake.files)

    def test_ssh_workspace_marker_rejects_nonempty_unmarked_workspace(self) -> None:
        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "~/.ssh/id_rsa", None, "/tmp/kernel-agent"),
            timeout_seconds=5,
        )
        runner.sftp = FakeSFTP(entries={"/tmp/kernel-agent": [FakeSFTPAttr("user_file.py")]})
        with self.assertRaisesRegex(RuntimeError, "not marked"):
            runner._ensure_workspace_marker("/tmp/kernel-agent")

    def test_ssh_workspace_clear_requires_marker_and_preserves_it(self) -> None:
        runner = SSHRunner(
            SSHConnectionInfo("example.invalid", 22, "user", "key", "~/.ssh/id_rsa", None, "/tmp/kernel-agent"),
            timeout_seconds=5,
        )
        marker = "/tmp/kernel-agent/.kernel_opt_agent_workspace"
        old_file = "/tmp/kernel-agent/old.py"
        fake = FakeSFTP(
            entries={"/tmp/kernel-agent": [FakeSFTPAttr(".kernel_opt_agent_workspace"), FakeSFTPAttr("old.py")]},
            files={marker, old_file},
        )
        runner.sftp = fake
        runner._clear_dir_contents("/tmp/kernel-agent")
        self.assertIn(marker, fake.files)
        self.assertIn(old_file, fake.removed)


if __name__ == "__main__":
    unittest.main()
