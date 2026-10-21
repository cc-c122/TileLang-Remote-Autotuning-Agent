"""Read-only query program executed verbatim by the SSH hardware adapter."""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid as uuid_module


def collect():
    result = {"devices": [], "environment": {"os": platform.platform(), "python_version": platform.python_version(), "python_executable": sys.executable}, "warnings": []}
    warnings = result["warnings"]
    environment = result["environment"]
    for package, field in (("tilelang", "tilelang_version"), ("mctilelang", "mctilelang_version"), ("mcTileLang", "mctilelang_version")):
        try:
            environment[field] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    for field in ("tilelang_version", "mctilelang_version"):
        if field not in environment:
            warnings.append(field + ": package metadata unavailable")
    tool_outputs = {}
    for name, arguments, field in (
        ("mx-smi", [], "driver_version"),
        ("mx-smi", ["-L"], None),
        ("nvidia-smi", ["--query-gpu=index,uuid,name,pci.bus_id,driver_version", "--format=csv,noheader"], "driver_version"),
        ("mxcc", ["--version"], "compiler_version"),
        ("nvcc", ["--version"], "compiler_version"),
    ):
        executable = shutil.which(name)
        if not executable:
            for directory in ("/opt/maca/bin", "/opt/maca/tools", "/usr/local/cuda/bin"):
                candidate = directory + "/" + name
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    executable = candidate
                    break
        if not executable:
            warnings.append(name + ": unavailable")
            continue
        try:
            completed = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=5)
            if completed.returncode:
                warnings.append(name + ": query_failed")
                continue
            output = completed.stdout[:65536]
            tool_outputs[name + " " + " ".join(arguments)] = output
            if field == "compiler_version" and output.strip():
                environment[field] = output.strip().splitlines()[-1][:256]
            if name == "mx-smi" and not arguments:
                match = re.search(r"Driver\s+Version\s*:\s*([\w.+-]+)", output, re.I)
                if match:
                    environment["driver_version"] = match.group(1)
                match = re.search(r"MACA\s+Version\s*:\s*([\w.+-]+)", output, re.I)
                if match:
                    environment["runtime_version"] = match.group(1)
        except Exception as exc:
            warnings.append(name + ": " + type(exc).__name__)
    try:
        import torch

        environment["torch_version"] = str(torch.__version__)
        runtime = getattr(torch.version, "maca", None)
        environment.setdefault("runtime_version", str(runtime or getattr(torch.version, "cuda", None) or "unknown"))
        if not torch.cuda.is_available():
            warnings.append("torch runtime: no visible GPU or driver unavailable")
            return result
        count = torch.cuda.device_count()
        environment["device_count"] = count
        nvidia_by_uuid = {}
        for line in tool_outputs.get("nvidia-smi --query-gpu=index,uuid,name,pci.bus_id,driver_version --format=csv,noheader", "").splitlines():
            values = [value.strip() for value in line.split(",")]
            if len(values) == 5 and values[0].isdigit():
                nvidia_by_uuid[values[1].lower()] = values
        for index in range(count):
            try:
                props = torch.cuda.get_device_properties(index)
                name = str(props.name)
                backend = "mxmaca" if runtime or re.search(r"metax|c500|mxc|maca", name, re.I) else "cuda"
                mask = os.environ.get("MACA_VISIBLE_DEVICES" if backend == "mxmaca" else "CUDA_VISIBLE_DEVICES")
                selectors = [value.strip() for value in mask.split(",")] if mask else []
                selector = selectors[index] if len(selectors) > index else str(index)
                device_warnings = []
                runtime_uuid = getattr(props, "uuid", None)
                uuid = None
                if runtime_uuid:
                    try:
                        uuid_text = str(runtime_uuid)
                        uuid_text = uuid_text[4:] if uuid_text.startswith("GPU-") else uuid_text
                        uuid = "GPU-" + str(uuid_module.UUID(uuid_text))
                    except (ValueError, TypeError, AttributeError):
                        device_warnings.append("runtime UUID format unavailable; no identity inferred from device order")
                pci = getattr(props, "pci_bus_id", None)
                if not isinstance(pci, str) or not re.fullmatch(r"[0-9a-fA-F]{4,8}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d", pci):
                    pci = None
                if pci:
                    pci = pci.lower()
                # Management indices, visibility masks and single-card counts do not
                # establish which physical GPU the runtime is using. Join by UUID only.
                if backend == "cuda" and uuid:
                    row = nvidia_by_uuid.get(uuid.lower())
                    if row:
                        environment["driver_version"] = row[4]
                identity = uuid or pci
                if not identity:
                    device_warnings.append("stable device UUID/PCI identity unavailable; task binding is disabled")
                fields = {
                    "target_name": name, "backend": backend, "device_count": count,
                    "device_id": backend + ":" + str(identity or "ordinal-" + str(index)),
                    "device_uuid": uuid, "pci_bus_id": str(pci) if pci else None,
                    "total_memory_bytes": getattr(props, "total_memory", None),
                    "warp_size": getattr(props, "warp_size", None),
                    "wave_size": getattr(props, "wave_size", None),
                    "sm_count": getattr(props, "multi_processor_count", None) if backend == "cuda" else None,
                    "cu_count": getattr(props, "compute_unit_count", None),
                    "runtime_multiprocessor_count": getattr(props, "multi_processor_count", None),
                    "max_threads_per_block": getattr(props, "max_threads_per_block", None),
                    "shared_memory_per_block_bytes": getattr(props, "shared_memory_per_block", None),
                }
                try:
                    fields["available_memory_bytes"] = int(torch.cuda.mem_get_info(index)[0])
                except Exception as exc:
                    device_warnings.append("available_memory_bytes: " + type(exc).__name__)
                for key, value in fields.items():
                    if value is None:
                        device_warnings.append(key + ": unavailable from runtime")
                result["devices"].append({"device_id": fields["device_id"], "runtime_index": index,
                    "selector": uuid or selector,
                    "identity_verified": bool(identity), "backend": backend, "fields": fields, "warnings": device_warnings})
            except Exception as exc:
                warnings.append("device " + str(index) + ": " + type(exc).__name__)
    except Exception as exc:
        warnings.append("torch runtime: " + type(exc).__name__)
    return result


if __name__ == "__main__":
    print("KERNEL_AGENT_GPU_QUERY=" + json.dumps(collect(), sort_keys=True, default=str))
