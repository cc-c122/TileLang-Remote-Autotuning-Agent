from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from kernel_opt_agent.config_model import load_config
from kernel_opt_agent.main import runner_exception_category
from kernel_opt_agent.agent.optimizer_policy import OptimizerPolicy
from kernel_opt_agent.kernel.template_manager import TemplateManager
from kernel_opt_agent.runner.command_guard import validate
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner
from kernel_opt_agent.storage.experiment_db import ExperimentDB


ROOT = Path(__file__).resolve().parents[1]


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

    def test_password_auth_cli_error_is_clear(self) -> None:
        config = yaml.safe_load((ROOT / "kernel_opt_agent" / "config.example.yaml").read_text(encoding="utf-8"))
        config["runner"]["type"] = "ssh"
        config["remote"]["auth_type"] = "password"
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "password.yaml"
            config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "main.py", "--config", str(config_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("SSH password authentication is reserved but not implemented", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_ssh_runner_exception_is_classified(self) -> None:
        config = load_config(str(ROOT / "kernel_opt_agent" / "config.ssh.example.yaml"))
        self.assertEqual(runner_exception_category(config, "timed out"), "ssh_connection_failed")
        self.assertEqual(runner_exception_category(config, "SFTP upload failed"), "sftp_upload_failed")


if __name__ == "__main__":
    unittest.main()
