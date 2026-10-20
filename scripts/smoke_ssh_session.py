"""Real HTTP/SSH smoke. Credentials are accepted in memory and never saved."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


GPU_SAMPLE = '''import argparse
import json
import statistics
import time
from pathlib import Path
import torch

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["correctness", "benchmark"], required=True)
args = parser.parse_args()
device = torch.device("cuda:0")
x_cpu = torch.arange(4096, dtype=torch.float32)
x = x_cpu.to(device)
def kernel():
    return x * 2.0 + 1.0
out = kernel()
torch.cuda.synchronize()
Path("results").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
if args.mode == "correctness":
    expected = x_cpu * 2.0 + 1.0
    error = float((out.cpu() - expected).abs().max())
    passed = torch.allclose(out.cpu(), expected, atol=0.0, rtol=0.0)
    Path("results/correctness.json").write_text(json.dumps({"passed": passed, "max_error": error, "atol": 0.0, "rtol": 0.0}))
    print(f'CORRECTNESS_RESULT status={"PASS" if passed else "FAIL"} max_error={error} reason="torch CPU reference"')
    raise SystemExit(0 if passed else 1)
for _ in range(3):
    kernel()
torch.cuda.synchronize()
samples = []
for _ in range(5):
    start = time.perf_counter()
    kernel()
    torch.cuda.synchronize()
    samples.append((time.perf_counter() - start) * 1000)
Path("results/benchmark.json").write_text(json.dumps({"samples_ms": samples, "median_ms": statistics.median(samples), "warmup": 3, "measurements": 5}))
Path("logs/remote.txt").write_text("correctness and benchmark used the selected visible GPU at runtime index 0")
print(f"BENCHMARK_RESULT latency_ms={statistics.median(samples)}")
'''


def http_json(base_url, path, payload=None, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base_url + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def run_smoke(base_url: str, ssh: dict, password: str, evidence_dir: Path) -> dict:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    session_id = None
    evidence = {"sample": "remote GPU elementwise operation with Torch CPU reference", "requests": [], "checks": {}, "passed": False}

    def record(path, status, public):
        text = json.dumps(public, default=str)
        for secret in (password, session_id, ssh.get("host"), ssh.get("username"), ssh.get("key_path")):
            if secret:
                text = text.replace(secret, "<redacted>")
        evidence["requests"].append({"path": path, "status": status, "response": json.loads(text)})

    try:
        status, created = http_json(base_url, "/api/ssh/sessions", {"ssh": ssh, "password": password})
        record("/api/ssh/sessions", status, {key: value for key, value in created.items() if key != "session_id"})
        if status != 200:
            evidence["failure_stage"] = "session_create"
            return evidence
        session_id = created["session_id"]
        evidence["checks"]["ssh_connected"] = True
        status, hardware = http_json(base_url, "/api/hardware/resolve", {"ssh_session_id": session_id})
        record("/api/hardware/resolve", status, hardware)
        if status != 200 or not hardware.get("devices"):
            evidence["failure_stage"] = "hardware_detection"
            return evidence
        device_id = hardware["devices"][0]["device_id"]
        request = {"project_name": "session-hardware-real-ssh-smoke", "ssh_session_id": session_id,
            "runner": {"type": "ssh"}, "sample": {"source_type": "inline", "entry_file": "kernel.py", "inline_text": GPU_SAMPLE},
            "target": {"device_id": device_id}, "commands": {"build_command": "python3 -m py_compile kernel.py",
                "correctness_command": "python3 kernel.py --mode correctness", "benchmark_command": "python3 kernel.py --mode benchmark"},
            "budget": {"max_iterations": 1, "candidates_per_iteration": 1, "timeout_seconds": 120},
            "profiler": {"enabled": False}, "patching": {"enabled": False}, "optimization": {"enabled": False}}
        status, task = http_json(base_url, "/api/tasks", request)
        record("/api/tasks", status, task)
        if status != 200:
            evidence["failure_stage"] = "task_create"
            return evidence
        task_id = task["task_id"]
        evidence["task_id"] = task_id
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status, state = http_json(base_url, "/api/tasks/" + task_id)
            if state.get("task", {}).get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        record("/api/tasks/{task_id}", status, state)
        status, result = http_json(base_url, "/api/tasks/" + task_id + "/results")
        record("/api/tasks/{task_id}/results", status, result)
        status, events = http_json(base_url, "/api/tasks/" + task_id + "/events")
        record("/api/tasks/{task_id}/events", status, events)
        checks = evidence["checks"]
        event_types = [item["type"] for item in events.get("events", [])]
        checks["correctness_before_benchmark"] = "correctness_started" in event_types and "benchmark_started" in event_types and event_types.index("correctness_started") < event_types.index("benchmark_started")
        results = result.get("results", {})
        checks["best_kernel"] = bool(results.get("best_kernel"))
        checks["latency_parsed"] = any(float(row.get("latency") or 0) > 0 for row in results.get("summary_table", []))
        evidence["results_dir"] = results.get("results_dir")
        for artifact in ("best_kernel", "report"):
            try:
                with urllib.request.urlopen(base_url + f"/api/tasks/{task_id}/download/{artifact}", timeout=30) as response:
                    content = response.read()
                    checks["download_" + artifact] = response.status == 200 and bool(content)
            except urllib.error.HTTPError:
                checks["download_" + artifact] = False
        root = Path(results["results_dir"])
        checks["hardware_snapshot"] = (root / "hardware_snapshot.yaml").is_file()
        checks["artifact_download"] = (root / "remote_artifacts/results/benchmark.json").is_file()
        checks["no_credential_leak"] = True
        for path in root.parent.rglob("*"):
            if path.is_file() and path.suffix in {".yaml", ".json", ".jsonl", ".csv", ".md", ".log", ".txt", ".py"}:
                content = path.read_bytes()
                if password.encode() in content or session_id.encode() in content:
                    checks["no_credential_leak"] = False
        evidence["passed"] = all(checks.values()) and state["task"]["status"] == "completed"
        if not evidence["passed"]:
            evidence["failure_stage"] = "task_execution"
        return evidence
    except Exception as exc:
        evidence["failure_stage"] = evidence.get("failure_stage", "http_smoke")
        evidence["exception_type"] = type(exc).__name__
        return evidence
    finally:
        if session_id:
            try:
                http_json(base_url, "/api/ssh/sessions/" + session_id, method="DELETE")
            except Exception as exc:
                evidence["cleanup_error_type"] = type(exc).__name__
        (evidence_dir / "smoke_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--username", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--password-env", default="KERNEL_AGENT_SSH_PASSWORD")
    parser.add_argument("--remote-workspace", default="/tmp/kernel_agent_session_smoke")
    parser.add_argument("--evidence-dir", type=Path, default=Path("kernel_opt_agent/workspace/server_runtime/ssh_smoke"))
    args = parser.parse_args()
    password = os.environ.get(args.password_env)
    if not password:
        parser.error("SSH password environment variable is not set")
    evidence = run_smoke(args.base_url, {"host": args.host, "port": args.port, "username": args.username,
        "host_key_fingerprint": args.fingerprint, "auth_type": "password", "remote_workspace": args.remote_workspace}, password, args.evidence_dir)
    print(json.dumps({"passed": evidence.get("passed", False), "checks": evidence.get("checks"), "failure_stage": evidence.get("failure_stage")}))


if __name__ == "__main__":
    main()
