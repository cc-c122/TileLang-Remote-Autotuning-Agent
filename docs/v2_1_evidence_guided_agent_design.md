# V2.1 Evidence-Guided Agent Design

V2.1 从 `v2-file-flow-freeze` 后进入 evidence-guided agent 阶段。目标是在现有文件驱动流程上，把 benchmark、profiler、编译日志、生成代码和历史 trial 结果整理成可审计证据，再由规则和 LLM 生成受控 patch 候选。

V2.1 不改变 V2 第一批 run request 契约：入口仍是 `schema_version: v2.run_request.v1`，真实运行仍通过：

```bash
python main.py --run-request run_request.yaml --settings settings.yaml
```

## 目标

Evidence-guided agent 的目标不是让 LLM 自由重写 kernel，而是形成一个可追踪闭环：

1. 收集每个 trial 的 benchmark、profiler、日志和生成代码证据。
2. 把缺失指标显式记为 `null`，并记录可用性。
3. 根据证据生成 bottleneck diagnosis。
4. 将 diagnosis 转换为小范围 patch intent。
5. 只在允许区域内生成 patch，并通过 build、correctness、benchmark 验证。
6. 保留所有证据、patch、失败原因和 best-seen 选择过程。

V2.1 仍然只返回当前预算内实际验证过的 best-seen kernel，不保证全局最优。

## Profiler Schema

Profiler 结果以 trial 为粒度记录。字段不可伪造；采集不到就写 `null`，并在 `available_metrics` 中标为 `false`。

```yaml
schema_version: v2.profiler_result.v1
trial_id: iter000_cand000
profiler:
  enabled: true
  type: dummy
  status: ok        # ok | skipped | failed
  error: null

metrics:
  latency_ms: 1.0
  tflops: 1.0
  estimated_hbm_bandwidth: 1.0
  register_count: null
  shared_memory_bytes: null
  private_memory_bytes: null
  occupancy: null
  warp_active_ratio: null
  hbm_read_bandwidth: null
  hbm_write_bandwidth: null
  shared_bank_conflict: null
  memory_coalescing_efficiency: null

available_metrics:
  latency_ms: true
  tflops: true
  estimated_hbm_bandwidth: true
  register_count: false
  shared_memory_bytes: false
  private_memory_bytes: false
  occupancy: false
  warp_active_ratio: false
  hbm_read_bandwidth: false
  hbm_write_bandwidth: false
  shared_bank_conflict: false
  memory_coalescing_efficiency: false

evidence_sources:
  benchmark_stdout: workspace/results/logs/iter000_cand000.stdout.log
  benchmark_stderr: workspace/results/logs/iter000_cand000.stderr.log
  compile_log: null
  generated_code: workspace/generated/iter000_cand000/kernel.py
  profiler_stdout: null
  profiler_stderr: null
```

Profiler 类型语义：

- `dummy`：只解析 benchmark 输出，通常只能提供 latency_ms、tflops、estimated_hbm_bandwidth。
- `tilelang_log`：解析 benchmark 输出、编译日志、benchmark stderr 和 generated code 中的结构化线索。
- `mxmaca`：保留真实 profiler 接口和日志解析框架；命令不可用时必须降级，不能填造 MXMACA 指标。

## Diagnosis Schema

Diagnosis 由 profiler metrics、benchmark、日志文本、generated code 和硬件字段共同支撑。缺字段只能形成 uncertainty，不能单独证明 bottleneck。

```yaml
schema_version: v2.diagnosis.v1
trial_id: iter000_cand000
summary:
  primary_bottleneck: memory_bound
  confidence: medium
  best_seen_context:
    objective: latency
    value: 1.0
    better: lower

diagnoses:
  - bottleneck_type: memory_bound
    confidence: medium
    evidence:
      - estimated_hbm_bandwidth=1.0 from benchmark_stdout
      - benchmark latency_ms available
    uncertainty:
      - hbm_read_bandwidth unavailable
      - hbm_write_bandwidth unavailable
      - hardware peak memory bandwidth unavailable
    recommended_actions:
      - vectorized_load
      - double_buffer
      - improve_thread_mapping
```

支持的 `bottleneck_type`：

- `compute_underutilization`
- `memory_bound`
- `excessive_hbm_write`
- `private_memory_spill`
- `shared_memory_pressure`
- `shared_bank_conflict`
- `poor_memory_coalescing`
- `low_occupancy`
- `pipeline_ineffective`
- `hardware_intrinsic_missing`

Confidence 规则：

- `high`：真实 profiler 多项证据一致，并且 benchmark/log 没有冲突。
- `medium`：benchmark + log 能互相支持，但 profiler-only 指标不完整。
- `low`：只有单一线索、日志关键词、benchmark 单项指标，或关键字段缺失。

没有 profiler 或 profiler 不可用时，系统退化为 benchmark + log based diagnosis。此时 profiler-only 指标保持 `null`，confidence 通常为 `low`，最多在 benchmark 与日志互相支持时为 `medium`。

## Patch 安全边界

Patch 阶段只允许把 diagnosis 转换成受控、可回滚的小改动。V2.1 禁止 LLM 自由改写整个工程，也禁止生成或执行任意 shell command。

允许的 patch 输入：

- 当前 trial 的 entry kernel 文件。
- profiler result、diagnosis、hardware fields、历史 trial summary。
- 受控 patch intent，例如 `vectorized_load`、`double_buffer`、`reduce_private_memory`、`shared_layout_swizzle`、`improve_thread_mapping`、`hardware_intrinsic_missing`。

允许的 patch 范围：

- 只修改 sample workspace 内的 `sample.entry_file`。
- 只修改明确标记或可定位的 kernel 局部区域。
- 只生成 unified diff 或等价结构化 patch。
- 每个 patch 必须记录 intent、evidence、before/after 摘要和应用状态。

禁止的 patch 行为：

- 修改 `settings.yaml`、secret、runner、remote、LLM 配置。
- 写入 SSH password、API key、token、私钥或私有 host。
- 修改仓库外路径、远程系统路径或 workspace 以外文件。
- 添加危险 shell command、安装/卸载系统包、删除系统目录。
- 绕过 `command_guard` 或扩大远程命令权限。
- 在 correctness 失败后仍把候选计入 best-seen。

Patch 验证顺序固定：

1. 应用 patch 到隔离 trial workspace。
2. 运行 build command。
3. 运行 correctness command。
4. correctness 通过后运行 benchmark command。
5. 收集 profiler/log evidence。
6. 更新 diagnosis 和 best-seen。

任何阶段失败都必须记录失败原因、日志路径和 patch 路径，但失败候选不能参与性能排名。

## 结果与报告

V2.1 输出应让用户能够复查：

- 每个 trial 的 profiler metrics 与 `available_metrics`。
- 每条 diagnosis 的 evidence、uncertainty、confidence。
- 每个 patch intent 的来源 diagnosis。
- build/correctness/benchmark/profiler 的状态与日志路径。
- 当前预算内的 best-seen kernel 和选择依据。

报告必须明确：best-seen 是当前预算内的观测结果，不是全局最优，也不是完整硬件/算法空间的最优证明。
