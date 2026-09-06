# V2 Evidence-Guided Kernel Optimization Agent 立项说明

## 1. 项目定位

V2 在 V1 TileLang Remote Autotuning Agent 的基础上，把系统从“参数搜索器”升级为“证据驱动的代码级优化 Agent”。

V1 已经能够通过 SSH/local runner 运行 TileLang kernel sample，生成参数组合，执行 build、correctness、benchmark，记录实验结果，并让 LLM 基于历史结果推荐下一批参数。V2 的核心变化是：Agent 不再只搜索 `BM/BN/BK/NUM_THREADS` 等模板参数，而是需要采集 benchmark、编译日志、生成代码和 profiler 信息，根据证据判断性能瓶颈，再生成受控 patch，对 kernel 的局部结构做优化，最后通过 correctness + benchmark 验证是否有效。

V2 仍然不承诺全局最优，只返回当前搜索预算和 patch 预算内实际验证过的 best-seen kernel。

## 2. V2 目标

1. 建立统一 profiler 接口，能够接收真实 profiler、TileLang 编译日志、生成代码和 benchmark 输出。
2. 基于证据诊断性能瓶颈，而不是让 LLM 凭空猜测。
3. 允许 LLM 或规则系统生成受控代码 patch，但只能修改授权 patch region。
4. 每个 patch 必须经过语法检查、编译、correctness 和 benchmark。
5. 失败 patch 自动 rollback，性能下降 patch 保留记录但不更新 best。
6. 支持单 patch 消融实验和有效 patch 组合实验。
7. 最终报告展示 profiler 指标、瓶颈证据、patch 假设、验证结果、性能曲线和不确定性。

## 3. 非目标与安全边界

1. 不承诺全局最优。
2. V2 不允许 LLM 自由重写整个 kernel 文件。
3. V2 不允许 LLM 生成或执行 shell command。
4. V2 不允许 patch 未授权文件或未标记区域。
5. profiler 指标不可用时必须为 `null`，不能伪造。
6. safe probe 和 profiler 解析结果不能被描述成官方硬件理论上限。
7. 所有远程命令仍必须经过 `command_guard`。
8. SSH password、LLM API key、token 等敏感信息不得写入日志、JSONL、CSV、报告或前端结果。

## 4. 核心判断问题

V2 的诊断需要围绕这些问题组织证据：

1. AP/计算核心是否没有跑满。
2. warp/wave 活跃度是否不足。
3. 是否产生 private/local memory spill。
4. global memory load/store 是否没有合并访问。
5. HBM read/write 带宽是否成为瓶颈。
6. 是否存在过多 HBM 写回。
7. shared memory 是否压力过高。
8. shared memory 是否产生 bank conflict。
9. pipeline/double buffer 是否没有有效隐藏访存延迟。
10. 是否没有使用硬件矩阵乘或目标 backend 的高性能 intrinsic。

如果证据不足，只能给 `low confidence` 诊断，并明确写出不确定性。

## 5. 新增目录结构

建议 V2 新增或扩展以下目录：

```text
kernel_opt_agent/
  profiler/
    base.py
    dummy_profiler.py
    tilelang_log_profiler.py
    mxmaca_profiler.py
    parser.py
  diagnosis/
    __init__.py
    bottleneck_rules.py
    evidence.py
    diagnosis_report.py
  patcher/
    __init__.py
    patch_region.py
    patch_generator.py
    patch_validator.py
    rollback.py
  optimizer/
    __init__.py
    ablation.py
```

说明：

1. V1 已有 `kernel_opt_agent/profiler/`，V2 在该目录上扩展，不要重复创建顶层 `profiler/`。
2. V1 已有 `agent/diagnosis.py`，V2 可以保留兼容入口，但新规则和结构化证据放入 `kernel_opt_agent/diagnosis/`。
3. V2 新增 `patcher/`，专门处理标记区域、patch 生成、patch 校验和 rollback。
4. V2 新增 `optimizer/ablation.py`，负责单 patch 和组合 patch 消融实验。

## 6. Profiler Interface

新增统一数据结构 `ProfilerResult`，至少包含：

```python
latency_ms: float | None
tflops: float | None
estimated_hbm_bandwidth: float | None
register_count: int | None
shared_memory_bytes: int | None
private_memory_bytes: int | None
occupancy: float | None
warp_active_ratio: float | None
hbm_read_bandwidth: float | None
hbm_write_bandwidth: float | None
shared_bank_conflict: float | None
memory_coalescing_efficiency: float | None
raw_logs: dict[str, str]
available_metrics: dict[str, bool]
```

规则：

1. 指标拿不到时字段必须是 `None`。
2. `available_metrics` 必须准确标记每个字段是否来自真实证据。
3. `dummy_profiler.py` 只解析 benchmark 输出。
4. `tilelang_log_profiler.py` 解析 TileLang 编译日志、生成代码、private/local memory、register/shared memory 相关线索。
5. `mxmaca_profiler.py` V2 第一阶段先实现命令接口、日志收集和解析框架，具体 MXMACA 指标可以 TODO，但不得伪造字段。
6. profiler 运行失败不能中断主流程，必须记录到 trial 结果。

## 7. Bottleneck Diagnosis

每条诊断记录结构：

```json
{
  "bottleneck_type": "memory_bound",
  "confidence": "medium",
  "evidence": ["hbm_read_bandwidth is high", "tflops is low"],
  "uncertainty": ["no hardware peak bandwidth available"],
  "recommended_actions": ["vectorized_load", "improve_thread_mapping"]
}
```

支持的 `bottleneck_type`：

1. `compute_underutilization`
2. `memory_bound`
3. `excessive_hbm_write`
4. `private_memory_spill`
5. `shared_memory_pressure`
6. `shared_bank_conflict`
7. `poor_memory_coalescing`
8. `low_occupancy`
9. `pipeline_ineffective`
10. `hardware_intrinsic_missing`

置信度规则：

1. 有真实 profiler 指标且多项证据一致，可以给 `high`。
2. 有 benchmark + 编译日志 + 生成代码证据，可以给 `medium`。
3. 只有 benchmark 或单条日志线索，只能给 `low`。
4. 缺少关键字段时必须写 `uncertainty`。

## 8. Controlled Patch Generation

V2 允许 kernel 文件出现以下 patch region：

```python
# BEGIN_AGENT_PATCH: load
# END_AGENT_PATCH

# BEGIN_AGENT_PATCH: compute
# END_AGENT_PATCH

# BEGIN_AGENT_PATCH: store
# END_AGENT_PATCH

# BEGIN_AGENT_PATCH: layout
# END_AGENT_PATCH
```

patch 规则：

1. LLM 和 rule-based patcher 只能修改这些区域。
2. 每次 patch 只能针对一种优化目标。
3. patch 必须包含 `optimization_name`、`target_region`、`hypothesis`、`expected_improvement`、`risk`、`diff`。
4. patch 应用前保存原文件 hash、完整 patch 文件和 git diff。
5. patch 应用后必须执行语法检查、build、correctness、benchmark。
6. 语法、编译、正确性失败必须自动 rollback。
7. benchmark 性能下降时不 rollback 记录本身，但不更新 best kernel。
8. patch 只能作用于 workspace 内文件。

## 9. 代码级优化模板

V2 至少支持以下优化模板：

| 模板 | 目标 | 适用证据 |
| --- | --- | --- |
| `vectorized_load` | 标量 global load 改为 vectorized load | `poor_memory_coalescing`, `memory_bound`, global load 多 |
| `double_buffer` | global->shared copy 与 compute 增加双缓冲 | `pipeline_ineffective`, latency 难隐藏 |
| `shared_layout_swizzle` | 调整 shared memory layout | `shared_bank_conflict` |
| `reduce_hbm_store` | 减少不必要 global store | `excessive_hbm_write` |
| `reduce_private_memory` | 降低寄存器/private memory 压力 | `private_memory_spill`, register 过高 |
| `improve_thread_mapping` | 调整线程到 tile 的映射 | `compute_underutilization`, `poor_memory_coalescing`, `low_occupancy` |

实现建议：

1. 第一阶段先做模板选择、region 修改、验证闭环，不要求每个模板都能对任意 TileLang kernel 产生最优代码。
2. 如果模板无法安全作用于当前 region，应返回 `not_applicable`，不要硬 patch。
3. 每个模板都需要有 rule-based fallback，LLM 不可用时仍能跑通最小闭环。

## 10. Ablation Study

新增 `kernel_opt_agent/optimizer/ablation.py`。

要求：

1. 每次只启用一个 patch，单独测试收益。
2. 已验证有效的 patch 可以进入组合测试。
3. 组合测试性能下降时要记录导致下降的组合。
4. 每个 patch 的单独收益、组合收益、失败原因和无效原因写入结果文件。
5. 最终 best kernel 只能来自 correctness 通过且 benchmark 最优的版本。

建议输出：

```text
workspace/results/patch_trials.jsonl
workspace/results/ablation_summary.csv
workspace/results/patches/
workspace/results/best_kernel.py
workspace/results/best_vs_baseline.diff
```

## 11. LLM JSON Schema

LLM 每轮输入：

1. 当前 best kernel 摘要。
2. baseline 性能。
3. top 5 实验结果。
4. failed cases。
5. `ProfilerResult`。
6. `BottleneckDiagnosis`。
7. 可修改 patch region。
8. 允许使用的优化模板。

LLM 输出必须是严格 JSON：

```json
{
  "diagnosis_summary": "...",
  "selected_optimization": "vectorized_load",
  "target_region": "load",
  "hypothesis": "...",
  "expected_improvement": "...",
  "risk": "...",
  "patch": "...",
  "validation_plan": "..."
}
```

约束：

1. 程序必须校验 JSON schema。
2. 非法 JSON retry 一次。
3. retry 后仍失败，fallback 到 rule-based patch。
4. `selected_optimization` 必须来自允许模板列表。
5. `target_region` 必须来自已发现 patch region。
6. `patch` 不允许包含 shell command。
7. `patch` 不允许修改未授权文件。
8. LLM 不允许伪造 profiler 指标。

## 12. Report 增强

V2 `report.md` 必须新增：

1. profiler 指标表。
2. 瓶颈诊断记录。
3. 每个 patch 的假设、证据、结果。
4. 每轮优化性能曲线。
5. best kernel 与 baseline 的 diff。
6. best-seen 提升比例。
7. 当前结论的不确定性。
8. 下一步建议。

前端 V2 应优先只读这些结果文件，暂不强制引入 HTTP API。

## 13. 前端主入口与持久设置

V2 的用户主路径不应要求用户手写 `config.yaml`。配置文件仍然作为内部可复现产物和高级入口保留，但普通用户应该通过前端完成一次运行所需的信息输入。

### 13.1 用户主流程

前端 V2 建议提供以下主流程：

1. 用户在前端输入或上传 TileLang 算子 sample。
2. 用户填写目标 GPU 型号，例如 `Metax C500`、`NVIDIA H100`、`A100` 或其他自定义名称。
3. 用户选择或填写 benchmark/correctness 命令；可以提供推荐默认值，但必须允许编辑。
4. 用户点击开始优化。
5. Agent 根据 GPU 型号、远程检测、内置 profile、文档缓存和 safe probe 自动补全硬件参数。
6. 前端展示自动补全的硬件参数来源、置信度和 unknown 字段。
7. 用户可以手动覆盖硬件参数；用户覆盖值优先级最高，并必须记录 source=`user_override` 或 `user_config`。
8. Agent 生成内部 effective config，执行 V1/V2 优化流程。
9. 前端只读展示 results、diagnosis、patch、ablation 和 report。

### 13.2 长期用户设置

SSH 容器信息和 LLM API 配置属于长期设置，不应该每次运行都要求用户重新填写。

建议前端提供设置页，长期保存：

1. SSH host。
2. SSH port。
3. SSH username。
4. SSH auth type。
5. SSH password 对应的环境变量名，或本地安全凭据引用。
6. remote workspace。
7. LLM provider。
8. LLM base URL。
9. LLM model。
10. LLM API key 对应的环境变量名，或本地安全凭据引用。

安全要求：

1. V2 不允许把 SSH password、LLM API key、token 明文写入仓库、日志、results、report 或前端导出的普通文件。
2. 如果实现本地持久化，密钥内容应优先使用系统 keyring；做不到时只能保存环境变量名，并要求用户在运行环境中设置。
3. 前端设置页可以显示“已配置/未配置”，但不显示完整密钥。
4. 生成的 `effective_config.yaml` 必须脱敏。

### 13.3 GPU 型号与硬件参数自动补全

用户只需要声明目标 GPU 型号和远程环境。Agent 负责尽量补全影响性能决策的硬件参数，例如：

1. SM/CU/AP 数量。
2. warp size 或 wave size。
3. 每个 block 最大线程数。
4. shared memory per block。
5. register 限制。
6. total/available memory。
7. vector alignment。
8. 支持 dtype。
9. matrix intrinsic 或 MMA 能力。
10. 理论 HBM 带宽，如果有可靠来源。

补全来源优先级：

1. 用户手动覆盖。
2. 远程自动探测。
3. 内置 hardware profile。
4. 官方文档缓存或用户提供文档。
5. safe probe。
6. unknown。

规则：

1. LLM 可以帮助根据 GPU 型号检索或整理公开资料，但不能把不确定信息伪造成事实。
2. 联网查文档必须由用户显式允许，并优先官方来源。
3. 没有可靠来源的字段保持 `null/unknown`。
4. safe probe 只能说明“当前小测试下可能可用/不可用”，不能声明官方理论上限。
5. 所有字段必须记录 `value/source/confidence/notes`。
6. 用户可以在前端修改自动补全结果；修改后必须进入审计记录。

### 13.4 前端与后端交互形态

V2 可以从文件驱动过渡到更完整的应用形态，但建议分阶段：

1. V2 第一阶段：前端生成或更新一个脱敏的 run request / config 文件，后端 CLI 读取该文件执行，前端继续只读结果文件。
2. V2 第二阶段：再考虑本地 HTTP API 或任务队列。
3. 不要在第一阶段为了 UI 引入不必要的服务端复杂度。

无论采用哪种形态，都必须保证每次运行能导出完整 `effective_config.yaml`，用于复现。

## 14. 内部配置建议

建议 V2 在 config 中新增：

```yaml
profiler:
  enabled: true
  type: dummy   # dummy | tilelang_log | mxmaca
  command: null
  timeout_seconds: 120
  log_paths: []

patching:
  enabled: true
  max_patch_rounds: 5
  max_patches_per_round: 2
  allowed_regions: ["load", "compute", "store", "layout"]
  allowed_optimizations:
    - vectorized_load
    - double_buffer
    - shared_layout_swizzle
    - reduce_hbm_store
    - reduce_private_memory
    - improve_thread_mapping

ablation:
  enabled: true
  max_combination_size: 2
```

兼容规则：

1. `config.yaml` 不再是普通用户必填入口，但仍是内部运行契约和高级入口。
2. 前端提交的 run request 必须转换成同等语义的 effective config。
3. 如果 `patching.enabled=false`，系统退化为 V1 参数搜索。
4. 如果 `profiler.enabled=false` 或真实 profiler 不可用，系统退化为 benchmark + log based diagnosis。
5. 如果 kernel 没有 patch region，系统只做诊断和参数搜索，不做代码 patch，并在报告中说明。

## 15. 开发优先级

建议按以下阶段推进：

1. 产品入口阶段：定义前端 run request、长期设置、effective config 生成和脱敏规则。
2. 硬件补全阶段：把 GPU 型号、hardware profile、remote detection、doc cache、safe probe 合并成统一 hardware info。
3. 数据结构阶段：实现 `ProfilerResult`、诊断记录、patch trial 记录、ablation 记录。
4. Profiler 阶段：实现 dummy profiler 和 TileLang log profiler；mxmaca profiler 先做接口和日志框架。
5. Diagnosis 阶段：实现规则诊断和 evidence 聚合。
6. Patch Region 阶段：实现 region 扫描、diff 限制、patch validator 和 rollback。
7. Patch Generation 阶段：实现 rule-based patch，再接 LLM patch JSON schema。
8. Validation 阶段：接入语法检查、build、correctness、benchmark。
9. Ablation 阶段：实现单 patch 和组合 patch 对比。
10. Report 阶段：增强 Markdown 报告和前端只读展示。
11. Remote Smoke 阶段：在真实 SSH 容器上验证失败隔离、日志拉回、敏感信息脱敏。

## 16. 线程分工建议

后端线程：

1. 先实现 run request 到 effective config 的转换和脱敏输出。
2. 实现 GPU 型号到 hardware info 的自动补全合并逻辑。
3. 实现 profiler 数据结构、dummy profiler、tilelang log parser。
4. 实现 bottleneck diagnosis rules。
5. 实现 patch region scanner、patch validator、rollback。
6. 实现 patch trial 执行闭环和 ablation。

前端线程：

1. 先设计 V2 主入口表单：sample、GPU 型号、benchmark/correctness 命令、开始优化。
2. 设计长期设置页：SSH 容器和 LLM API 配置。
3. 展示自动补全硬件参数，允许用户覆盖，并显示 source/confidence。
4. 展示 profiler 指标表、诊断卡片、patch trial 列表、ablation summary。
5. 展示 best vs baseline diff 和性能曲线。
6. 所有缺失文件显示 waiting/empty，不崩溃。

文档线程：

1. 更新 README 的 V2 能力说明，但不要覆盖 V1 安装使用说明。
2. 新增“用户不必手写 config”的 V2 使用说明。
3. 新增 GPU 型号自动补全、用户覆盖和来源置信度说明。
4. 新增 V2 配置说明和 patch region 写法。
5. 新增 profiler 指标不可用时的降级说明。
6. 新增“不保证全局最优”和“profiler 不可伪造”的安全说明。

## 17. 需要进一步确认的问题

这些问题不阻塞立项，但需要在实现前确认：

1. MXMACA profiler 的实际命令名称、输出格式和可用权限。
2. TileLang 生成代码文件的默认位置是否稳定，是否需要用户在 config 中显式提供。
3. patch region 是否只放在 `kernel.entry_file`，还是允许未来扩展到同一 sample 目录下的多个文件。建议 V2 第一阶段只允许 `entry_file`。
4. V2 patch 预算如何配置：按 round 数、patch 数，还是总 trial 数。建议三者都记录，但先以 `max_patch_rounds` 为主。
5. 真实远程容器是否允许运行 profiler 命令；如果不允许，必须保持 log-based fallback。
6. V2 第一阶段前端是否只生成 run request 文件，还是直接引入本地 HTTP API。建议先使用 run request 文件。
7. 本地长期设置是否能使用系统 keyring；如果不能，密钥只保存环境变量名。
8. GPU 官方文档联网查询是否默认关闭。建议默认关闭，由用户显式开启。

## 18. V2 完成标准

1. 没有真实 profiler 时，能退化为 benchmark + log based optimization。
2. 有 profiler 日志时，能解析并用于瓶颈诊断。
3. 能识别 patch region 并生成受控 patch。
4. 能自动执行语法检查、编译、correctness 和 benchmark。
5. 能比较 patch 前后性能。
6. 能 rollback 失败 patch。
7. 能记录性能下降 patch 但不更新 best。
8. 能做单 patch 消融和有效 patch 组合实验。
9. 能生成完整 V2 report。
10. 普通用户可以通过前端输入 sample 和 GPU 型号启动任务，不必手写 `config.yaml`。
11. SSH 容器和 LLM API 配置可以作为长期设置复用，且敏感值不进入日志和结果文件。
12. GPU 硬件参数可以自动补全，用户可以覆盖，每个字段都有 source/confidence。
13. 所有失败都进入结构化结果文件，不允许静默跳过。
