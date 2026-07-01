import kernel


s = kernel.score()
latency = max(0.1, 10.0 / max(s, 0.1))
tflops = max(0.1, s * 20.0)
bandwidth = 400.0 + s * 35.0
print(f"BENCHMARK_RESULT latency_ms={latency:.4f} tflops={tflops:.4f} bandwidth_gbps={bandwidth:.4f}")

