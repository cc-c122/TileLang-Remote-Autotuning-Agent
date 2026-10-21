// Native GPU collector smoke, not a TileLang or Paged Attention baseline.
#include <mc_runtime.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <vector>

static void check(mcError_t status) {
    if (status != mcSuccess) {
        std::cerr << "GPU runtime error: " << mcGetErrorString(status) << "\n";
        std::exit(1);
    }
}

extern "C" __global__ void profiler_vector_axpby(const float* input, float* output, int count) {
    int index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < count) output[index] = input[index] * 2.0f + 1.0f;
}

int main(int argc, char** argv) {
    const char* mode = argc == 3 && std::strcmp(argv[1], "--mode") == 0 ? argv[2] : "correctness";
    const bool profile = std::strcmp(mode, "profile") == 0;
    const bool benchmark = std::strcmp(mode, "benchmark") == 0;
    const int count = 32768, threads = 256, blocks = count / threads;
    std::vector<float> input(count), output(count);
    for (int i = 0; i < count; ++i) input[i] = float(i % 251);
    float *device_input = nullptr, *device_output = nullptr;
    check(mcSetDevice(0));
    check(mcMalloc(reinterpret_cast<void**>(&device_input), count * sizeof(float)));
    check(mcMalloc(reinterpret_cast<void**>(&device_output), count * sizeof(float)));
    check(mcMemcpy(device_input, input.data(), count * sizeof(float), mcMemcpyHostToDevice));
    auto launch = [&]() {
        profiler_vector_axpby<<<blocks, threads>>>(device_input, device_output, count);
        check(mcGetLastError());
    };
    for (int i = 0; i < 3; ++i) launch();
    check(mcDeviceSynchronize());
    std::vector<float> samples;
    mcEvent_t start, stop;
    if (benchmark) {
        check(mcEventCreate(&start));
        check(mcEventCreate(&stop));
        for (int i = 0; i < 5; ++i) {
            check(mcEventRecord(start));
            launch();
            check(mcEventRecord(stop));
            check(mcEventSynchronize(stop));
            float elapsed = 0.0f;
            check(mcEventElapsedTime(&elapsed, start, stop));
            samples.push_back(elapsed);
        }
        check(mcEventDestroy(start));
        check(mcEventDestroy(stop));
    } else {
        if (profile) check(mcProfilerStart());
        launch();
        check(mcDeviceSynchronize());
        if (profile) check(mcProfilerStop());
    }
    check(mcMemcpy(output.data(), device_output, count * sizeof(float), mcMemcpyDeviceToHost));
    float max_error = 0.0f;
    for (int i = 0; i < count; ++i) max_error = std::max(max_error, std::fabs(output[i] - (input[i] * 2.0f + 1.0f)));
    check(mcFree(device_input));
    check(mcFree(device_output));
    std::cout << "CORRECTNESS_RESULT status=" << (max_error == 0 ? "PASS" : "FAIL")
              << " max_error=" << max_error << " reason=\"CPU reference; atol=0 rtol=0\"\n";
    if (max_error != 0) return 1;
    if (benchmark) {
        auto sorted = samples;
        std::sort(sorted.begin(), sorted.end());
        std::ofstream log("results/benchmark.json");
        log << std::setprecision(12) << "{\"warmup\":3,\"measurements\":5,\"timing\":\"GPU events\",\"samples_ms\":[";
        for (size_t i = 0; i < samples.size(); ++i) log << (i ? "," : "") << samples[i];
        log << "],\"median_ms\":" << sorted[2] << "}";
        std::cout << std::setprecision(12) << "BENCHMARK_RESULT latency_ms=" << sorted[2] << "\n";
    }
    return 0;
}
