import os

import kernel


print(f"BENCHMARK_CWD {os.getcwd()}")
latency = max(0.1, 20.0 / max(kernel.score(), 0.1))
tflops = max(0.1, kernel.score() * 10.0)
bandwidth = 300.0 + kernel.score() * 20.0
print(f"BENCHMARK_RESULT latency_ms={latency:.4f} tflops={tflops:.4f} bandwidth_gbps={bandwidth:.4f}")
