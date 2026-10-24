// Counter calibration only. This is not the TileLang optimization workload.
#include <mc_runtime.h>
#include <maca_fp16.h>
#include <__clang_maca_mma_functions.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
#include <chrono>
#include <vector>

static void check(mcError_t status) {
    if (status != mcSuccess) {
        std::cerr << "GPU runtime error: " << mcGetErrorString(status) << "\n";
        std::exit(1);
    }
}

extern "C" __global__ void attribution_elementwise(float* output) {
    output[blockIdx.x * blockDim.x + threadIdx.x] = float(threadIdx.x) * 2.0f + 1.0f;
}

extern "C" __global__ void attribution_mma(float* output) {
    namespace w = mxmaca::wmma;
    w::fragment<w::matrix_a, 16, 16, 16, __half, w::row_major> a;
    w::fragment<w::matrix_b, 16, 16, 16, __half, w::col_major> b;
    w::fragment<w::accumulator, 16, 16, 16, float> c;
    w::fill_fragment(a, __float2half(1.0f));
    w::fill_fragment(b, __float2half(2.0f));
    w::fill_fragment(c, 0.0f);
    for (int i = 0; i < 128; ++i) w::mma_sync(c, a, b, c);
    w::store_matrix_sync(output + blockIdx.x * 256, c, 16, w::mem_row_major);
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const std::string mode = argv[1];
    if (mode != "idle" && mode != "elementwise" && mode != "mma") return 2;
    check(mcSetDevice(0));
    float* device_output = nullptr;
    const int count = 128 * 256;
    check(mcMalloc(reinterpret_cast<void**>(&device_output), count * sizeof(float)));
    auto launch = [&]() {
        if (mode == "elementwise") attribution_elementwise<<<128, 256>>>(device_output);
        if (mode == "mma") attribution_mma<<<128, 64>>>(device_output);
        check(mcGetLastError());
    };
    for (int i = 0; i < 3; ++i) launch();
    check(mcDeviceSynchronize());
    check(mcProfilerStart());
    if (mode == "idle") std::this_thread::sleep_for(std::chrono::milliseconds(20));
    else launch();
    check(mcDeviceSynchronize());
    check(mcProfilerStop());
    float max_error = 0.0f;
    if (mode != "idle") {
        std::vector<float> output(count);
        check(mcMemcpy(output.data(), device_output, count * sizeof(float), mcMemcpyDeviceToHost));
        for (int i = 0; i < count; ++i) {
            const float expected = mode == "mma" ? 4096.0f : float(i % 256) * 2.0f + 1.0f;
            max_error = std::isfinite(output[i]) ? std::max(max_error, std::fabs(output[i] - expected))
                                                : std::numeric_limits<float>::infinity();
        }
    }
    check(mcFree(device_output));
    std::cout << "CALIBRATION_CONTROL mode=" << mode << " max_error=" << max_error << "\n";
    return max_error == 0.0f ? 0 : 1;
}
