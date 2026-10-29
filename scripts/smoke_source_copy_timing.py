"""Independent CPU timing smoke, never a GPU or required-speedup assertion."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path


def run_smoke(size=200000, warmup=3, repeat=5):
    if size < 1 or warmup < 3 or repeat < 5:
        raise ValueError("positive size, >=3 warmups and >=5 measured calls are required")
    source = list(range(size))
    output = [0] * size

    def loop():
        for index in range(size):
            output[index] = source[index]

    def bulk_copy():
        output[:] = source

    records = {}
    for name, function in (("baseline_loop", loop), ("slice_copy", bulk_copy)):
        function()
        if output != source:
            raise ValueError("CPU reference comparison failed")
        for _ in range(warmup):
            function()
        samples = []
        for _ in range(repeat):
            start = time.perf_counter()
            function()
            samples.append((time.perf_counter() - start) * 1000)
        if not all(math.isfinite(value) and value > 0 for value in samples):
            raise ValueError("invalid actual timing sample")
        ordered = sorted(samples)
        position = (len(ordered) - 1) * 0.9
        lower = int(position)
        p90 = ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)
        records[name] = {"raw_samples_ms": samples, "median_ms": statistics.median(samples), "p90_ms": p90,
                         "stddev_ms": statistics.stdev(samples), "warmup_runs": warmup, "measurement_runs": repeat}
    before, after = (records[key]["median_ms"] for key in ("baseline_loop", "slice_copy"))
    return {"evidence_kind": "independent_cpu_smoke_not_gpu_evidence", "correctness_passed": output == source,
            "size": size, "records": records, "observed_improvement_percent": (before - after) / before * 100,
            "speedup_required": False, "note": "No production performance threshold or acceptance decision is changed by this smoke."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=200000)
    parser.add_argument("--output")
    args = parser.parse_args()
    serialized = json.dumps(run_smoke(args.size), indent=2)
    if args.output:
        from kernel_opt_agent.main import WORKSPACE_ROOT

        path = Path(args.output).resolve()
        path.relative_to(WORKSPACE_ROOT.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
