from __future__ import annotations

from typing import Any


def diagnose(records: list[dict[str, Any]]) -> list[str]:
    ok = [r for r in records if r.get("status") == "benchmark_ok"]
    failed = [r for r in records if r.get("status") != "benchmark_ok"]
    notes = []
    if not ok:
        notes.append("No benchmark-passing candidate was observed; inspect correctness/build/run failures first.")
    else:
        best = ok[0]
        metrics = best.get("metrics", {})
        if metrics.get("tflops") is not None and metrics.get("latency") is not None:
            notes.append("Best-seen result has usable latency and TFLOPS metrics; further profiler data is needed before making a firm bottleneck claim.")
        if metrics.get("bandwidth") is not None:
            notes.append("Bandwidth is available; compare it with hardware limits to judge whether the kernel may be memory-bound.")
    if failed:
        notes.append(f"{len(failed)} candidates failed; common causes should be checked in failed_cases.jsonl.")
    notes.append("These are conservative hints, not confirmed profiler conclusions.")
    return notes

