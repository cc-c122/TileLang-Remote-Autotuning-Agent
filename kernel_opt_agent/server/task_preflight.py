from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kernel_opt_agent.config_model import resolve_path
from kernel_opt_agent.file_task import FileTaskError, audit_files, dependency_command
from kernel_opt_agent.redaction import redact_data, redact_text
from kernel_opt_agent.sample_security import copy_safe_sample_contents


def run_file_preflight(config, results_dir: Path, should_cancel, execution_context=None) -> None:
    from kernel_opt_agent.main import build_runner

    contract = config.file_task
    if contract is None:
        return
    report = {"status": "running", "runner": config.runner.type, "dependencies": [],
              "gpu_check": "local_demo_no_gpu_validation" if config.runner.type == "local" else "not_checked",
              "notes": ["Only unconditional module-level imports are checked in preflight; conditional/deferred imports remain subject to actual execution."]}
    sample = resolve_path(config.kernel.sample_path)
    expected = {**contract.protected_files, config.kernel.entry_file: hashlib.sha256((sample / config.kernel.entry_file).read_bytes()).hexdigest()}
    manifest = {"entry_file": config.kernel.entry_file, "entry_sha256": expected[config.kernel.entry_file],
                "test_files": contract.test_files.model_dump(), "protected_files": contract.protected_files,
                "input_mode": "files" if contract.attest_entry else "advanced_commands",
                "entry_execution_attestation": contract.attest_entry,
                "runner_mode": "local_demo" if config.runner.type == "local" else "ssh"}
    (results_dir / "file_task_manifest.json").write_text(json.dumps(redact_data(config, manifest), indent=2) + "\n", encoding="utf-8")
    runner = None
    try:
        if should_cancel():
            report["status"] = "cancelled"
            return
        if config.runner.type == "ssh" and (execution_context is None or not execution_context.hardware_snapshot or not execution_context.hardware_snapshot.get("execution_device_verified")):
            raise FileTaskError("gpu_preflight_failed", "a verified, device-bound SSH session is required")
        preflight_workspace = results_dir.parent / "preflight_workspace"
        copy_safe_sample_contents(sample, preflight_workspace)
        runner = build_runner(config, preflight_workspace)
        audit_files(runner, expected)
        if config.runner.type == "ssh":
            report["gpu_check"] = "verified_session_device"
        command = dependency_command(config.runner.type, contract)
        if hasattr(runner, "run_cancellable"):
            result = runner.run_cancellable("file_task_preflight", command, should_cancel, timeout_seconds=config.search.timeout_seconds)
        else:
            result = runner.run("file_task_preflight", command)
        for name, content in (("stdout", result.stdout), ("stderr", result.stderr)):
            (results_dir / f"preflight.{name}.log").write_text(redact_text(config, content) or "", encoding="utf-8")
        audit_files(runner, expected)
        if should_cancel() and result.returncode in {0, 130} and not result.timeout and not result.guard_denied:
            report["status"] = "cancelled"
            return
        if result.guard_denied:
            raise FileTaskError("guard_denied", "preflight command rejected by command guard")
        if result.timeout:
            raise FileTaskError("preflight_timeout", "Python/dependency preflight timed out")
        if result.returncode:
            raise FileTaskError("python_environment_unavailable", "Python/dependency preflight command failed")
        rows = [line for line in result.stdout.splitlines() if line.startswith("FILE_TASK_PREFLIGHT_RESULT ")]
        if len(rows) != 1:
            raise FileTaskError("python_environment_unavailable", "Python preflight result missing or ambiguous")
        environment = json.loads(rows[0].split(" ", 1)[1])
        report.update(environment)
        if environment.get("python_compatible") is not True:
            raise FileTaskError("python_environment_unavailable", "file-driven tasks require Python >= 3.10")
        missing = [row["module"] for row in environment["dependencies"] if row["available"] is not True]
        if missing:
            raise FileTaskError("dependency_missing", "required Python dependencies unavailable: " + ", ".join(missing))
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error_category=getattr(exc, "code", "preflight_failed"), exception_type=type(exc).__name__)
        raise
    finally:
        cleanup_error = None
        if runner is not None and hasattr(runner, "close"):
            try:
                runner.close()
            except Exception as exc:
                cleanup_error = type(exc).__name__
                report["cleanup_error_type"] = cleanup_error
        cleanup_failed = cleanup_error and report["status"] != "failed"
        if cleanup_failed:
            report.update(status="failed", error_category="preflight_cleanup_failed")
        (results_dir / "preflight.json").write_text(json.dumps(redact_data(config, report), indent=2) + "\n", encoding="utf-8")
        if cleanup_failed:
            raise FileTaskError("preflight_cleanup_failed", "preflight runner cleanup failed")
