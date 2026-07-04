# V2 Profiler Diagnosis

本文说明 V2 第二批 profiler diagnosis 的字段语义、缺失值处理、瓶颈证据来源和置信度规则。它不定义新的 run request schema；run request 仍统一使用 `schema_version: v2.run_request.v1`。

## Profiler 字段含义

`profiler.enabled` 表示是否尝试采集 profiler 证据。`false` 时系统不会要求真实 profiler 指标，诊断退化为 benchmark + log based diagnosis。

`profiler.type` 表示证据采集器类型：

- `dummy`：只解析 benchmark stdout 中的 `latency`、`tflops` 和 `bandwidth`。
- `tilelang_log`：解析 benchmark 输出、benchmark stderr、TileLang 编译日志和生成代码里的结构化线索。
- `mxmaca`：保留 MXMACA profiler 接口和日志解析框架；真实命令接入前，只解析显式出现在 profiler stdout/stderr 或 compile log 中的指标，不能伪造指标。

Profiler 输出字段：

- `latency`：benchmark 解析到的延迟，通常越低越好。
- `tflops`：benchmark 解析到的吞吐。
- `estimated_hbm_bandwidth`：benchmark 输出中的带宽估计。
- `register_count`：编译日志或 profiler 日志中的寄存器数量。
- `shared_memory_bytes`：shared memory 使用量。
- `private_memory_bytes`：private/local memory 使用量，常用于判断 spill 风险。
- `occupancy`：占用率，范围通常按 0 到 1 解释。
- `warp_active_ratio`：warp 活跃比例，低值通常表示并行度或流水隐藏不足。
- `hbm_read_bandwidth`：HBM 读带宽。
- `hbm_write_bandwidth`：HBM 写带宽。
- `shared_bank_conflict`：shared memory bank conflict 线索。
- `memory_coalescing_efficiency`：内存合并访问效率，低值提示 coalescing 问题。
- `raw_logs`：用于审计的原始日志片段或日志来源。
- `available_metrics`：每个指标是否真实可用；只有解析到值或明确采集到值时才是 `true`。

## 为什么缺失指标是 null

指标缺失时必须写成 `null`，不能填 0、估算值或“看起来合理”的默认值。`null` 表示当前证据链没有采集到该指标，常见原因包括：

- profiler 未启用或真实 profiler 不可用。
- benchmark 输出没有对应字段。
- 编译日志、生成代码或 profiler 日志没有可解析模式。
- 远程容器权限不足，无法运行真实 profiler。
- 当前 profiler 类型只支持部分指标，例如 `dummy` 只解析 benchmark 指标。

`null` 不是性能为 0，也不是硬件不支持；它只表示证据缺失。诊断规则必须把缺失项写入 uncertainty，并降低 confidence。

## Bottleneck 证据来源

每个 bottleneck 必须由 profiler 指标、benchmark 指标、日志文本或硬件字段支持。没有证据时只能给 `low` confidence，并说明缺失项。

| bottleneck_type | 主要证据来源 | 典型证据 |
| --- | --- | --- |
| `compute_underutilization` | `occupancy`、`warp_active_ratio`、`tflops` | `occupancy < 0.5`、`warp_active_ratio < 0.6`、缺少 peak TFLOPS 时保留不确定性 |
| `memory_bound` | `estimated_hbm_bandwidth`、`hbm_read_bandwidth`、`hbm_write_bandwidth`、硬件内存带宽字段 | 观测带宽接近硬件带宽，或只有观测带宽但缺少硬件峰值时降级 |
| `excessive_hbm_write` | `hbm_write_bandwidth`、`hbm_read_bandwidth` | 写带宽较高，或写带宽超过读带宽 |
| `private_memory_spill` | `private_memory_bytes`、编译日志 local/private memory 线索 | private/local memory 大于 0 |
| `shared_memory_pressure` | `shared_memory_bytes`、`shared_memory_per_block_bytes` | shared memory 使用量接近 per-block 限制 |
| `shared_bank_conflict` | `shared_bank_conflict`、profiler/log bank conflict 线索 | bank conflict 指标大于 0 |
| `poor_memory_coalescing` | `memory_coalescing_efficiency` | coalescing efficiency 低于 0.8 |
| `low_occupancy` | `occupancy` | `occupancy < 0.5` |
| `pipeline_ineffective` | `warp_active_ratio`、`estimated_hbm_bandwidth`、`occupancy` | warp 活跃比例低，或 occupancy 不低但内存带宽很低 |
| `hardware_intrinsic_missing` | compile log、generated code、profiler raw logs | 日志出现 missing tensor/mma intrinsic、no tensor、no mma 等线索 |

证据来源优先级从强到弱是：真实 profiler 指标、多来源日志和 benchmark 一致、单一日志关键词、缺字段推断。缺字段本身不能证明 bottleneck，只能作为 uncertainty。

## Confidence 规则

- `high`：profiler 多项证据一致，例如真实 profiler 同时显示低 occupancy、低 warp active ratio，并且 benchmark 指标也符合该判断。
- `medium`：benchmark + log 能互相支持，例如 benchmark 带宽异常，同时编译日志显示 shared/private/register 相关线索。
- `low`：只有单一线索、只有日志关键词、只有 benchmark 指标、或关键字段缺失。

系统不能为了提高 confidence 而伪造 profiler 指标。只要关键证据缺失，就必须在 diagnosis 的 `uncertainty` 中写清楚，并把 confidence 降到 `low` 或最多 `medium`。

## 没有 profiler 时的降级

当 `profiler.enabled=false`、真实 profiler 不可运行、profiler 日志缺失，或 `profiler.type=dummy` 只能解析 benchmark 时，系统退化为 benchmark + log based diagnosis。

降级模式下仍可以给出保守建议，但必须满足：

- profiler-only 指标保持 `null`。
- evidence 只能引用实际 benchmark 输出、compile log、generated code 或 runner 日志。
- uncertainty 明确写出缺失的 profiler 指标。
- confidence 通常为 `low`；只有 benchmark 与日志互相支持时才可为 `medium`。

V2 仍然只返回当前预算内实际测到的 best-seen kernel，不保证全局最优。Profiler diagnosis 是解释和引导下一步搜索/patch 的证据，不是最优性证明。
