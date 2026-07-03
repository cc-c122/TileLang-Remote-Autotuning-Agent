# V2 User Flow

V2 的目标是让普通用户通过前端创建优化任务，而不是手写 `config.yaml`。配置文件仍作为内部复现产物和高级入口保留。

## 第一次设置 SSH / LLM

用户第一次使用时进入 Settings 页面，配置长期复用的信息。

SSH 设置：

- SSH host。
- SSH port。
- SSH username。
- auth type，V2 默认使用 password auth，key auth 可作为兼容路径。
- password 的环境变量名，例如 `KERNEL_AGENT_SSH_PASSWORD`。
- remote workspace。

LLM 设置：

- LLM provider。
- base URL。
- model。
- API key 的环境变量名，例如 `OPENAI_API_KEY`。

安全要求：

1. 前端可以显示“已配置/未配置”，但不显示完整密钥。
2. SSH password、LLM API key 和 token 不得写入仓库、日志、results、report 或导出的普通文件。
3. 如果没有安全 keyring，只保存环境变量名，由用户在运行环境中设置真实值。
4. 生成的 `effective_config.yaml` 必须脱敏。

## 新建优化任务

用户在前端点击新建任务，填写本次运行相关信息：

1. 输入或上传 TileLang sample。
2. 选择 `entry_file`，即可被渲染或 patch 的入口文件。
3. 填写目标 GPU 型号，例如 `Metax C500`、`NVIDIA H100`、`A100` 或自定义名称。
4. 填写 correctness 命令。
5. 填写 build 命令，可选。
6. 填写 benchmark 命令。
7. 填写搜索预算，例如最大 iteration、候选数、timeout。
8. 选择是否启用 V2 evidence-guided patching。

前端提交 run request 后，后端生成脱敏 `effective_config.yaml` 并开始执行。V2 第一阶段可以继续由前端写 run request 文件、后端 CLI 读取该文件，前端只读结果文件。

## GPU 参数自动补全

用户只需要声明目标 GPU 型号和远程环境。Agent 尝试补全影响优化决策的硬件参数：

- SM / CU / AP 数量。
- warp size 或 wave size。
- 每个 block 最大线程数。
- shared memory per block。
- register 限制。
- total / available memory。
- vector alignment。
- 支持 dtype。
- matrix intrinsic / MMA 能力。
- 理论 HBM 带宽，如果有可靠来源。

补全来源优先级：

1. 用户覆盖。
2. 远程自动探测。
3. 内置 hardware profile。
4. 官方文档缓存或用户提供文档。
5. safe probe。
6. unknown。

自动补全不是准确性保证。每个字段必须展示：

- `value`
- `source`
- `confidence`
- `notes`

没有可靠来源的字段必须保持 `unknown` 或 `null`。safe probe 只能说明“当前小测试下可能可用/不可用”，不能声明官方硬件理论上限。

## 用户覆盖硬件参数

前端应允许用户查看和覆盖自动补全的硬件字段。

覆盖规则：

1. 用户覆盖值优先级最高。
2. 覆盖字段的 `source` 应记录为 `user_override` 或 `user_config`。
3. 覆盖前后的值都应进入审计记录。
4. 用户覆盖不应删除原始自动探测证据。
5. 如果用户清空覆盖值，字段回到自动合并结果。

覆盖适用于自动探测不完整、文档缓存过旧、safe probe 不充分或用户知道更准确硬件参数的场景。

## 查看结果

任务运行中和运行后，前端只读展示结果文件。

V1/V2 通用结果：

- baseline 和 best-seen 性能。
- trial 状态。
- failed cases。
- stdout / stderr 路径。
- `best_kernel.py`。
- `best_config.yaml`。
- `report.md`。

V2 结果：

- profiler 指标表。
- bottleneck diagnosis。
- 每个 patch 的假设、证据、diff 和验证结果。
- ablation summary。
- best vs baseline diff。
- 性能曲线。
- 不确定性和缺失证据。

文件缺失时应显示 waiting 或 empty，不崩溃。

V2 仍然只返回当前搜索预算和 patch 预算内实际验证过的 best-seen kernel，不保证全局最优。
