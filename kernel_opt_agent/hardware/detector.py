from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kernel_opt_agent.config_model import AppConfig
from kernel_opt_agent.runner.local_runner import LocalRunner
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner

from .generic_linux_detector import GenericLinuxDetector
from .hardware_info import CANONICAL_FIELDS, HardwareInfo
from .profile_loader import HardwareProfileLoader


def _profile_field_values(profile: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in CANONICAL_FIELDS:
        if name in profile:
            values[name] = profile[name]
    mma = profile.get("mma")
    if isinstance(mma, dict) and "supported_dtypes" in mma and "supported_dtypes" not in values:
        values["supported_dtypes"] = mma["supported_dtypes"]
    return values


def _write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_detection_runner(config: AppConfig, results_dir: Path):
    if config.runner.type == "local":
        return LocalRunner(results_dir.parent, config.hardware_detection.timeout_seconds, config.constraints.denied_commands)
    info = SSHConnectionInfo(
        host=config.remote.host,
        port=config.remote.port,
        username=config.remote.username,
        auth_type=config.remote.auth_type,
        key_path=config.remote.key_path,
        password_env=config.remote.password_env,
        remote_workspace=config.remote.remote_workspace,
    )
    runner = SSHRunner(info, config.hardware_detection.timeout_seconds, config.constraints.denied_commands)
    runner.connect()
    return runner


def detect_hardware(config: AppConfig, results_dir: Path, profile_loader: HardwareProfileLoader | None = None, command_runner: Any | None = None) -> HardwareInfo:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_lines = ["hardware detection started"]
    info = HardwareInfo.unknown()
    info.detection_enabled = config.hardware_detection.enabled
    info.conservative_mode = config.hardware_detection.conservative_unknown_mode
    loader = profile_loader or HardwareProfileLoader()

    try:
        if not config.hardware_detection.enabled:
            log_lines.append("hardware_detection.enabled=false; skipping automatic detection")

        profile_name = config.hardware.profile or config.hardware.target_name or "unknown_gpu"
        if config.hardware_detection.builtin_profile:
            loaded_name, profile = loader.load(profile_name)
            info.profile_used = loaded_name
            log_lines.append(f"loaded builtin profile: {loaded_name}")
            for field_name, value in _profile_field_values(profile).items():
                if field_name not in info.fields:
                    continue
                if value is not None:
                    info.set_field(field_name, value, "builtin_profile", "medium", f"from built-in {loaded_name} profile")
                else:
                    info.set_field(field_name, None, "unknown", "unknown", f"not specified by built-in {loaded_name} profile")
        else:
            log_lines.append("builtin profile loading disabled")

        if config.hardware_detection.enabled and config.hardware_detection.remote_detection:
            info.remote_detection_attempted = True
            runner = command_runner
            close_runner = False
            try:
                if runner is None:
                    runner = _build_detection_runner(config, results_dir)
                    close_runner = True
                detected, remote_log_lines = GenericLinuxDetector(runner).detect()
                log_lines.extend(remote_log_lines)
                for field_name, value in detected.items():
                    if field_name in info.fields:
                        info.set_field(field_name, value, "remote_detection", "medium", "from read-only remote detection")
            except Exception as exc:
                log_lines.append(f"remote detection failed: {exc}")
                info.warnings.append(f"remote detection failed: {exc}")
            finally:
                if close_runner and hasattr(runner, "close"):
                    try:
                        runner.close()
                    except Exception as exc:
                        log_lines.append(f"remote detection runner close failed: {exc}")

        if config.hardware.target_name:
            info.set_field("target_name", config.hardware.target_name, "user_config", "high", "from config hardware.target_name")
        if config.hardware.backend:
            info.set_field("backend", config.hardware.backend, "user_config", "high", "from config hardware.backend")
        for field_name, value in config.hardware.fields.items():
            info.set_field(field_name, value, "user_config", "high", f"from config hardware.fields.{field_name}")

        if config.hardware_detection.doc_lookup or config.hardware.allow_doc_lookup:
            log_lines.append("doc lookup requested but not implemented in first-stage detector")
        if config.hardware_detection.safe_probe:
            log_lines.append("safe probe requested but not implemented in first-stage detector")

        unknowns = info.unknown_fields()
        if unknowns:
            info.warnings.append(f"{len(unknowns)} hardware fields remain unknown")
            log_lines.append(f"unknown fields: {', '.join(unknowns)}")
    except Exception as exc:
        info = HardwareInfo.unknown()
        info.warnings.append(f"hardware detection failed: {exc}")
        log_lines.append(f"hardware detection failed: {exc}")
    finally:
        detected_path = results_dir / "hardware_detected.yaml"
        with detected_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(info.to_dict(), f, sort_keys=True)
        _write_log(results_dir / "hardware_detection.log", log_lines)
    return info
