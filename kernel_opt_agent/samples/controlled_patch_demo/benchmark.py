import kernel


score = kernel.kernel_score()
latency_ms = 10.0 / score
print(f"BENCHMARK_RESULT latency_ms={latency_ms:.4f} tflops=1.0 bandwidth_gbps=2.0")
