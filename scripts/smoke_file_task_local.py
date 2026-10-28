"""Real loopback HTTP file intake; CPU baseline only, never GPU evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from kernel_opt_agent.main import WORKSPACE_ROOT
from kernel_opt_agent.server.security import validate_loopback_host


FILES = {
    "kernel.py": "def run(values):\n    return [value * 2 for value in values]\n",
    "reference.py": "def reference(values):\n    return [value + value for value in values]\n",
    "correctness.py": '''from kernel import run
from reference import reference
values = list(range(4096))
error = max(abs(a-b) for a,b in zip(run(values), reference(values)))
status = 'PASS' if error == 0 else 'FAIL'
print(f'CORRECTNESS_RESULT status={status} max_error={error} reason="CPU demo fixed reference; tolerance=0"')
''',
    "benchmark.py": '''import json
import statistics
import time
from kernel import run
values = list(range(4096))
for _ in range(3): run(values)
samples = []
for _ in range(5):
    start = time.perf_counter_ns()
    run(values)
    elapsed = time.perf_counter_ns() - start
    assert elapsed > 0
    samples.append(elapsed / 1000000)
print('CPU_DEMO_RAW_SAMPLES_MS ' + json.dumps(samples))
print(f'BENCHMARK_RESULT latency_ms={statistics.median(samples)}')
''',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8884")
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    validate_loopback_host(address.hostname or "")
    if address.scheme != "http" or address.username or address.password:
        raise ValueError("a credential-free loopback HTTP URL is required")
    with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False) as client:
        response = client.post("/api/samples/upload", files=[("files", (name, source.encode(), "text/plain")) for name, source in FILES.items()], data={"entry_file": "kernel.py"})
        response.raise_for_status()
        response = client.post("/api/tasks", json={
            "project_name": "CPU-DEMO-file-intake-http-smoke",
            "sample": {"source_type": "upload", "upload_id": response.json()["upload_id"], "entry_file": "kernel.py"},
            "test_files": {"correctness": "correctness.py", "benchmark": "benchmark.py"},
            "runner": {"type": "local"}, "profiler": {"enabled": False}, "budget": {"timeout_seconds": 30},
        })
        response.raise_for_status()
        task_id = response.json()["task_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            task = client.get(f"/api/tasks/{task_id}").json()["task"]
            if task["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        else:
            client.post(f"/api/tasks/{task_id}/cancel")
            raise TimeoutError("file HTTP smoke timed out")
        result = client.get(f"/api/tasks/{task_id}/results").json()["results"]
        assert task["status"] == "completed", task.get("error")
        assert task["execution_mode"] == "baseline_only"
        assert result["source_optimization"]["status"] == "unavailable"
        assert result["source_optimization"]["reason"]
        assert result["improvement_percent"] is None
        assert result["preflight"]["status"] == "passed"
        assert result["preflight"]["gpu_check"] == "local_demo_no_gpu_validation"
        assert result["summary_table"][0]["correctness"]["passed"] is True
        assert float(result["summary_table"][0]["latency"]) > 0
        downloads = {}
        for name in ("best_kernel", "report"):
            response = client.get(f"/api/tasks/{task_id}/download/{name}")
            response.raise_for_status()
            downloads[name] = hashlib.sha256(response.content).hexdigest()
        assert downloads["best_kernel"] == hashlib.sha256(FILES["kernel.py"].encode()).hexdigest()
        events = client.get(f"/api/tasks/{task_id}/events").json()["events"]
        names = [row["type"] for row in events]
        assert names.index("correctness_started") < names.index("benchmark_started")
        receipt = {"status": "passed", "agent_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                   "task_id": task_id, "validation_scope": "real HTTP / real CPU local baseline; no GPU or optimized speedup claim",
                   "task_status": task["status"], "execution_mode": task["execution_mode"],
                   "preflight": result["preflight"], "summary_table": result["summary_table"],
                   "source_optimization": result["source_optimization"], "downloads_sha256": downloads,
                   "events": names, "results_dir": result["results_dir"]}
        output = WORKSPACE_ROOT / "server_runtime/file_task_http_smoke.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "passed", "task_id": task_id, "execution_mode": task["execution_mode"], "receipt": str(output)}))


if __name__ == "__main__":
    main()
