"""Collect bounded, repeated C500 MMA controls, without granting attribution.

Uses SSHRunner and the normal collector/guard. The connection config is private;
the password is read from an existing environment variable or an unechoed prompt.
No installation, global environment mutation, or unrelated process cleanup.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import sys
import uuid

from pydantic import SecretStr
import yaml

from kernel_opt_agent.hardware.remote_gpu_detector import query_remote_devices
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case
from kernel_opt_agent.profiler.mcprofiler.attribution_validation import ControlCapture, MmaCalibration
from kernel_opt_agent.profiler.mcprofiler.collector import McProfilerCollectionRequest, collect_remote_mcprofiler_case
from kernel_opt_agent.runner.host_keys import HostKeyStore
from kernel_opt_agent.runner.ssh_runner import SSHConnectionInfo, SSHRunner


ROOT = Path(__file__).resolve().parents[1]
CONTROL_SOURCE = ROOT / "kernel_opt_agent/samples/profiler_smoke/attribution_controls.cpp"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-config", type=Path, required=True)
    parser.add_argument("--host-key-store", type=Path, required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load(args.connection_config.read_text(encoding="utf-8"))
    remote = config["remote"]
    password = os.environ.get(remote.get("password_env") or "KERNEL_AGENT_SSH_PASSWORD")
    if not password:
        password = getpass.getpass("Memory-only SSH password: ")
    workspace = "/tmp/kernel-agent-mma-calibration-" + uuid.uuid4().hex[:12]
    runner = SSHRunner(SSHConnectionInfo(
        host=remote["host"], port=remote["port"], username=remote["username"], auth_type="password",
        key_path=None, password_env=None, remote_workspace=workspace,
        host_key_fingerprint=remote.get("host_key_fingerprint"), connect_timeout_seconds=20,
    ), args.timeout_seconds, password=SecretStr(password), host_key_store=HostKeyStore(args.host_key_store))
    private_values = [password, remote["host"], remote["username"], workspace]

    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            for private in private_values:
                if private:
                    value = value.replace(private, "<redacted>")
        return value

    def save(name, payload):
        (args.output_dir / name).write_text(json.dumps(sanitize(payload), sort_keys=True, indent=2), encoding="utf-8")

    def command(stage, text):
        result = runner.run(stage, text)
        save(stage + ".json", {**asdict(result), "command": text})
        if result.returncode != 0 or result.timeout or result.guard_denied:
            raise RuntimeError(stage + "_failed")
        return result.stdout

    stage = "connect"
    try:
        runner.connect()
        stage = "device_query"
        query = query_remote_devices(runner)
        devices = [device for device in query["devices"] if device.get("identity_verified")]
        if len(devices) != 1:
            raise RuntimeError("unique_verified_device_required")
        device = devices[0]
        device_id = "device_sha256:" + hashlib.sha256(device["device_id"].encode()).hexdigest()
        private_values.extend([device["device_id"], device["fields"].get("device_uuid")])
        runner.execution_env = {"MACA_VISIBLE_DEVICES": device["selector"], "CUDA_VISIBLE_DEVICES": device["selector"]}
        fields = {**device["fields"], "device_id": device_id, "device_uuid": None, "pci_bus_id": None}
        save("environment.json", {"device_id": device_id, "environment": query["environment"], "fields": fields})
        stage = "inventory"
        version_output = command("tool_version", shlex.quote(args.client) + " version")
        tool_version = re.search(r"version\s*:\s*([0-9.]+)", version_output).group(1)
        build = re.search(r"hash\s*:\s*([0-9a-f]+)", version_output).group(1)
        command("tool_help", shlex.quote(args.client) + " help")
        command("tool_metrics", shlex.quote(args.client) + " show_metrics")
        command("tool_files", "python3 -c " + shlex.quote(
            "import json; from pathlib import Path; p=Path(" + repr(posixpath.dirname(args.client)) + "); "
            "print(json.dumps([{'name':x.name,'directory':x.is_dir(),'size':x.stat().st_size} for x in sorted(p.iterdir())][:100]))"))
        command("compiler_version", "/opt/maca/mxgpu_llvm/bin/mxcc --version")
        runner.upload(CONTROL_SOURCE, posixpath.join(workspace, CONTROL_SOURCE.name))
        source_hash = hashlib.sha256(CONTROL_SOURCE.read_bytes()).hexdigest()
        if runner.file_sha256(CONTROL_SOURCE.name) != source_hash:
            raise RuntimeError("uploaded_source_hash_mismatch")
        compiler = "/opt/maca/mxgpu_llvm/bin/mxcc -x maca -O2 -offload-arch native --maca-path=/opt/maca "
        stage = "build_controls"
        command(stage, compiler + "attribution_controls.cpp -L/opt/maca/lib -lmcToolsExt -o attribution_controls")
        command("compile_device_ir", compiler + "-maca-device-only -S -emit-llvm attribution_controls.cpp -o attribution_controls.ll")
        runner.download("attribution_controls.ll", args.output_dir / "attribution_controls.ll")
        ir = (args.output_dir / "attribution_controls.ll").read_text(encoding="utf-8")
        functions = re.findall(r"define[^\n]+@([^\s(]+)[^\n]*\{(.*?)^\}", ir, re.M | re.S)
        counts = {name: len(re.findall(r"\bcall\b[^\n]*@llvm\.mxc\.mma", body)) for name, body in functions}
        save("codegen.json", {"level": "device_llvm_ir_not_final_isa", "mma_calls_by_function": counts})
        generated_hash = hashlib.sha256((args.output_dir / "attribution_controls.ll").read_bytes()).hexdigest()
        captures = []
        cases = []
        for repeat in (1, 2):
            for mode in ("idle", "elementwise", "mma"):
                stage = f"control_{mode}_{repeat}"
                command(stage + "_correctness", "./attribution_controls " + mode)
                request = McProfilerCollectionRequest(
                    target_command="./attribution_controls " + mode, case_name=stage,
                    source_trial_id=stage, source_sha256=source_hash, metrics=("AP MMA Duty ratio",),
                    kernel_names=() if mode == "idle" else ("attribution_" + mode,),
                    executable=args.client, server_executable=args.server,
                    timeout_seconds=args.timeout_seconds, per_kernel=mode != "idle", profile_from_start=False,
                    device_id=device_id, shape_id="calibration_only", dtype="float16" if mode == "mma" else "float32",
                )
                collection = collect_remote_mcprofiler_case(runner, args.output_dir, request,
                    redactor=sanitize, secret_values=(password,), append_imported_records=True)
                case = collection.to_dict(sanitize)
                cases.append({"mode": mode, "repeat": repeat, **case})
                value, report_hash = None, ""
                if collection.case_dir:
                    case_dir = args.output_dir / collection.case_dir
                    report = case_dir / collection.primary_report if collection.primary_report else next(case_dir.rglob("report_dumped_result.json"), None)
                    if report and report.is_file():
                        parsed = parse_mcprofiler_case(report)
                        observation = next((item for item in parsed.observations if item.metric_name == "mma_duty"), None)
                        value = observation.value if observation and observation.available else None
                        report_hash = hashlib.sha256(report.read_bytes()).hexdigest()
                captures.append(ControlCapture(mode, value, source_hash, generated_hash,
                    0 if mode == "idle" else counts.get("attribution_" + mode), report_hash))
                save("captures.json", cases)
                print(json.dumps({"stage": stage, "status": collection.status, "reason": collection.reason_category, "mma_duty": value}), flush=True)
        calibration = MmaCalibration(tool_version, build, device["fields"].get("target_name"), tuple(captures))
        evaluation = calibration.evaluate()
        save("calibration.json", {"inputs": asdict(calibration), "result": evaluation})
        print(json.dumps({"calibration": evaluation, "output_dir": str(args.output_dir)}))
        return 0 if evaluation["status"] == "verified" else 2
    except Exception as exc:
        save("failure.json", {"status": "blocked", "stage": stage, "exception_type": type(exc).__name__, "reason": str(exc)})
        print(json.dumps({"status": "blocked", "stage": stage, "exception_type": type(exc).__name__}))
        return 1
    finally:
        runner.close()


if __name__ == "__main__":
    sys.exit(main())
