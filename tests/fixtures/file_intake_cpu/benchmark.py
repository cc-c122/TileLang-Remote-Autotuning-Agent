import json
import statistics
import time

from kernel import run

values = list(range(4096))
for _ in range(3):
    run(values)
samples = []
for _ in range(5):
    start = time.perf_counter_ns()
    run(values)
    samples.append((time.perf_counter_ns() - start) / 1000000)
print("CPU_DEMO_RAW_SAMPLES_MS " + json.dumps(samples))
print(f"BENCHMARK_RESULT latency_ms={statistics.median(samples)}")
