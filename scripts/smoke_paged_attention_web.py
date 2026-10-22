"""Real HTTP/SSH Paged Attention validation. No credentials are persisted."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def run_smoke(base_url: str, ssh: dict, password: str, evidence_dir: Path) -> dict:
    from kernel_opt_agent.main import WORKSPACE_ROOT
    from kernel_opt_agent.server.security import validate_loopback_host

    address = urlsplit(base_url)
    validate_loopback_host(address.hostname or "")
    if address.scheme != "http" or address.username or address.password:
        raise ValueError("smoke requires a credential-free loopback HTTP URL")

    evidence_dir.resolve().relative_to(WORKSPACE_ROOT.resolve())
    evidence_dir.mkdir(parents=True, exist_ok=True)
    sample = Path(__file__).resolve().parents[1] / "kernel_opt_agent/samples/paged_attention_decode"
    session_id = None
    device_id = None
    identifiers: list[str] = []
    evidence = {"operator": "Paged Attention Decode", "status": "blocked", "tasks": [],
                "protocol": {"seed": 42, "warmup": 3, "measurements": 5, "atol": 0.002, "rtol": 0.002},
                "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sample.glob("*.py"))}}

    def sanitize(value):
        if isinstance(value, dict):
            return {k: sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        if isinstance(value, str):
            for secret in (password, session_id, device_id, ssh.get("host"), ssh.get("username"), ssh.get("remote_workspace"), *identifiers):
                if secret:
                    value = value.replace(secret, "<redacted>")
        return value

    with httpx.Client(base_url=base_url, timeout=120, trust_env=False) as client:
        try:
            created = client.post("/api/ssh/sessions", json={"ssh": ssh, "password": password})
            if created.status_code != 200:
                evidence["failure"] = {"stage": "ssh_session", "status": created.status_code, "response": created.json()}
                return evidence
            session_id = created.json()["session_id"]
            resolved = client.post("/api/hardware/resolve", json={"ssh_session_id": session_id})
            hardware = resolved.json()
            device_id = hardware.get("selected_device_id")
            if resolved.status_code != 200 or not device_id:
                evidence["failure"] = {"stage": "hardware_resolve", "response": hardware}
                return evidence
            for device in hardware.get("devices", []):
                for obj in (device, device.get("fields") or {}):
                    identifiers.extend(str(obj[key]) for key in ("selector", "device_uuid", "pci_bus_id") if obj.get(key))
            evidence["hardware"] = sanitize(hardware)
            files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in sorted(sample.iterdir())
                     if p.is_file() and p.suffix in {".py", ".md"}]
            uploaded = client.post("/api/samples/upload", files=files,
                                   data={"entry_file": "_upstream_sparse_gqa_decode_paged.py"})
            uploaded.raise_for_status()
            upload_id = uploaded.json()["upload_id"]
            for context in (128, 512):
                command = f"python3 run_paged_attention_decode.py --context-length {context} --warmup 3 --repeat 5"
                payload = {
                    "project_name": f"c500-paged-attention-attribution-ctx{context}",
                    "ssh_session_id": session_id, "runner": {"type": "ssh"},
                    "sample": {"source_type": "upload", "upload_id": upload_id,
                               "entry_file": "_upstream_sparse_gqa_decode_paged.py"},
                    "target": {"gpu_model": "MetaX C500", "backend": "mxmaca", "device_id": device_id},
                    "commands": {"build_command": command + " --mode build",
                                 "correctness_command": command + " --mode correctness",
                                 "benchmark_command": command + " --mode benchmark"},
                    "optimization": {"enabled": True, "max_candidates": 1, "benchmark_repeats": 5,
                                     "min_improvement_percent": 1.0},
                    "budget": {"timeout_seconds": 180, "max_iterations": 1, "candidates_per_iteration": 1},
                    "profiler": {"enabled": True, "type": "mxmaca", "auto_collect": True},
                }
                response = client.post("/api/tasks", json=payload)
                response.raise_for_status()
                task_id = response.json()["task_id"]
                record = {"task_id": task_id, "context_length": context}
                evidence["tasks"].append(record)
                deadline = time.monotonic() + 1200
                while time.monotonic() < deadline:
                    task = client.get(f"/api/tasks/{task_id}").json()["task"]
                    if task["status"] in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(1)
                else:
                    client.post(f"/api/tasks/{task_id}/cancel")
                    record["status"] = "timeout_cancel_requested"
                    continue
                results = client.get(f"/api/tasks/{task_id}/results").json()["results"]
                events = client.get(f"/api/tasks/{task_id}/events").json()
                record.update({"status": task["status"], "execution_mode": task["execution_mode"],
                               "results_dir": results["results_dir"],
                               "source_optimization": results.get("source_optimization"),
                               "summary_table": results.get("summary_table"), "events": events.get("events"),
                               "profiler_collections": results.get("profiler_collections")})
                (evidence_dir / f"ctx{context}.json").write_text(json.dumps(sanitize(record), indent=2), encoding="utf-8")
            if len(evidence["tasks"]) == 2 and all(
                row.get("source_optimization", {}).get("baseline_verified") is True
                and row["source_optimization"].get("trials") for row in evidence["tasks"]
            ):
                evidence["status"] = "executed"
            return evidence
        except Exception as exc:
            evidence["failure"] = {"stage": "http_smoke", "exception_type": type(exc).__name__}
            return evidence
        finally:
            if session_id:
                try:
                    client.delete("/api/ssh/sessions/" + session_id)
                except Exception as exc:
                    evidence["cleanup_error_type"] = type(exc).__name__
            sanitized = sanitize(evidence)
            evidence.clear()
            evidence.update(sanitized)
            (evidence_dir / "web_smoke.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
