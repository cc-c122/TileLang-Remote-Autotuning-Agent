from __future__ import annotations

import hashlib
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hardware_info import HardwareInfo
from .profile_loader import HardwareProfileLoader


QUERY_SCRIPT = Path(__file__).with_name("remote_gpu_query.py").read_text(encoding="utf-8")
_BOOTSTRAP = (
    "import glob,json,os,subprocess,sys,time\n"
    f"query_script={QUERY_SCRIPT!r}\n"
    """candidates=[sys.executable,'/opt/conda/bin/python3','/opt/conda/bin/python','/root/miniconda3/bin/python']
candidates+=sorted(glob.glob('/opt/conda/envs/*/bin/python'))[:4]
deadline=time.monotonic()+50
warnings=[]
selected=None
seen=set()
for candidate in dict.fromkeys(candidates):
    if not os.path.isfile(candidate):
        continue
    resolved=os.path.realpath(candidate)
    if resolved in seen:
        continue
    seen.add(resolved)
    remaining=deadline-time.monotonic()
    if remaining<=0:
        warnings.append('interpreter discovery: total_timeout')
        break
    label='interpreter['+str(len(seen)-1)+']'
    try:
        completed=subprocess.run([candidate,'-B','-c',query_script],capture_output=True,text=True,timeout=min(15,remaining))
        if completed.returncode:
            warnings.append(label+': query_failed')
            continue
        line=next(line for line in reversed(completed.stdout.splitlines()) if line.startswith('KERNEL_AGENT_GPU_QUERY='))
        payload=json.loads(line.split('=',1)[1])
        if not isinstance(payload.get('devices'),list) or not isinstance(payload.get('environment'),dict):
            raise ValueError()
        devices=payload['devices']
        if selected is None or devices and not selected.get('devices'):
            selected=payload
        if any(isinstance(item,dict) and item.get('identity_verified') is True and item.get('fields') for item in devices):
            selected=payload
            break
        warnings.append(label+(': stable_identity_unavailable' if devices else ': no_visible_gpu'))
    except subprocess.TimeoutExpired:
        warnings.append(label+': timeout')
    except Exception as exc:
        warnings.append(label+': '+type(exc).__name__)
if selected is None:
    selected={'devices':[],'environment':{},'warnings':[]}
selected['warnings']=list(dict.fromkeys(selected.get('warnings',[])+warnings))
print('KERNEL_AGENT_GPU_QUERY='+json.dumps(selected,sort_keys=True))
"""
)
GPU_QUERY_COMMAND = "python3 -B -c " + shlex.quote(_BOOTSTRAP)
UNITS = {"total_memory_bytes": "bytes", "available_memory_bytes": "bytes", "shared_memory_per_block_bytes": "bytes",
         "sm_count": "SM", "cu_count": "CU", "warp_size": "threads", "wave_size": "threads", "max_threads_per_block": "threads"}


def query_remote_devices(runner: Any, *, device_bound: bool = False) -> dict[str, Any]:
    detected_at = datetime.now(timezone.utc).isoformat()
    try:
        execute = getattr(runner, "run_bound_detection", runner.run) if device_bound else getattr(runner, "run_detection", runner.run)
        response = execute("hardware_gpu_query", GPU_QUERY_COMMAND)
        if response.returncode or response.guard_denied or response.timeout:
            reason = "guard_denied" if response.guard_denied else "timeout" if response.timeout else "command_failed"
            return {"devices": [], "environment": {}, "warnings": ["GPU query: " + reason], "detected_at": detected_at}
        line = next(line for line in reversed(response.stdout.splitlines()) if line.startswith("KERNEL_AGENT_GPU_QUERY="))
        payload = json.loads(line.split("=", 1)[1])
        if not isinstance(payload.get("devices"), list) or not isinstance(payload.get("environment"), dict):
            raise ValueError()
        payload["detected_at"] = detected_at
        return payload
    except Exception as exc:
        return {"devices": [], "environment": {}, "warnings": ["GPU query: " + type(exc).__name__], "detected_at": detected_at}


def device_signature(device: dict[str, Any]) -> str:
    fields = device["fields"]
    identity = {key: fields.get(key) for key in ("device_id", "device_uuid", "pci_bus_id", "target_name", "backend", "total_memory_bytes")}
    identity.update({"selector": device.get("selector"), "runtime_index": device.get("runtime_index")})
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def resolve_remote_hardware(payload: dict[str, Any], selected_device_id: str | None,
                            user_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    devices = payload.get("devices", [])
    selected = next((item for item in devices if item.get("device_id") == selected_device_id), None)
    if selected_device_id is None and len(devices) == 1:
        selected = devices[0]
    warnings = list(payload.get("warnings", []))
    info = HardwareInfo.unknown()
    info.remote_detection_attempted = True
    if selected:
        fields = selected.get("fields", {})
        profile_name, profile = HardwareProfileLoader().load(fields.get("target_name"))
        info.profile_used = profile_name
        # Only a profile matching the observed device may fill unknown fields.
        for key, value in profile.items():
            if key in info.fields and value is not None and key not in {"target_name", "backend"}:
                info.set_field(key, value, "builtin_profile", "medium", "profile fallback; not remotely measured")
        combined = {**payload.get("environment", {}), **fields}
        for byte_name, gb_name in (("total_memory_bytes", "total_memory_GB"), ("available_memory_bytes", "available_memory_GB")):
            if isinstance(combined.get(byte_name), (int, float)):
                combined[gb_name] = combined[byte_name] / 1_000_000_000
        for key, value in combined.items():
            if value is not None and value != "unknown":
                notes = "runtime multiprocessor count; SM/CU semantics are not inferred" if key == "runtime_multiprocessor_count" else "read-only GPU runtime query"
                info.set_field(key, value, "remote_detection", "high", notes)
                info.fields[key].unit = "GB" if key.endswith("_GB") else UNITS.get(key)
                info.fields[key].detected_at = payload.get("detected_at")
            elif key in info.fields and info.fields[key].value is None:
                info.fields[key].unit = UNITS.get(key)
                info.fields[key].detected_at = payload.get("detected_at")
                info.fields[key].notes = "unavailable from remote query"
        warnings.extend(selected.get("warnings", []))
    elif selected_device_id:
        warnings.append("selected device not found")
    elif len(devices) > 1:
        warnings.append("device_id is required for a multi-device host")
    else:
        warnings.append("no visible GPU could be detected")
    for key, value in (user_overrides or {}).items():
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        info.set_field(key, value, "user_override", "high", "explicit user hardware override")
        info.fields[key].unit = UNITS.get(key)
    info.warnings = list(dict.fromkeys(warnings))
    required = ("target_name", "backend", "device_id", "total_memory_bytes", "max_threads_per_block", "shared_memory_per_block_bytes")
    measured = selected is not None and all(selected.get("fields", {}).get(key) is not None for key in required)
    status = "complete" if measured and not warnings else "partial" if devices else "failed"
    return {"hardware": info.to_dict(), "devices": devices, "selected_device_id": selected.get("device_id") if selected else None,
            "detection_status": status, "warnings": info.warnings, "detected_at": payload.get("detected_at")}
