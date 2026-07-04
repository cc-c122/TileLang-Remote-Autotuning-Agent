# V2 Run Request Schema

这是 V2 第一批唯一合法 run request schema：`schema_version: v2.run_request.v1`。后端、前端、文档必须严格对齐，不能各写一套变体。

```yaml
schema_version: v2.run_request.v1
project_name: my-task

sample:
  source_type: inline   # inline | path | upload
  inline_text: "..."
  path: null
  entry_file: kernel.py

commands:
  build_command: python -m py_compile kernel.py
  correctness_command: python correctness.py
  benchmark_command: python benchmark.py

target:
  gpu_model: Metax C500
  backend: unknown

settings_ref:
  use_saved_settings: true

hardware_overrides:
  fields: {}

search:
  strategy: rule_based
  max_iterations: 1
  candidates_per_iteration: 3
  timeout_seconds: 60
  objective: latency

profiler:
  enabled: true
  type: dummy

patching:
  enabled: true
```

## 字段规则

- `schema_version` 必须是 `v2.run_request.v1`。
- `project_name` 是用户可读任务名。
- `sample.source_type` 只能是 `inline`、`path` 或 `upload`。
- `sample.inline_text` 只在 `source_type: inline` 时承载代码文本。
- `sample.path` 只在 `source_type: path` 或 `source_type: upload` 时承载路径；inline 模式下必须是 `null`。
- `sample.entry_file` 是 sample 内的入口文件。
- `commands.build_command`、`commands.correctness_command`、`commands.benchmark_command` 分别对应构建、正确性检查和性能测试。
- `target.gpu_model` 是用户输入或选择的 GPU 型号。
- `target.backend` 第一批可以是 `unknown`，由后端在 effective config 阶段继续解析。
- `settings_ref.use_saved_settings` 表示使用已保存的 SSH / LLM 设置；run request 不直接携带 secret。
- `hardware_overrides.fields` 保存用户主动覆盖的硬件字段；空对象表示不覆盖。
- `search.strategy` 第一批固定使用 `rule_based`。
- `search.objective` 第一批使用 `latency`。
- `profiler.enabled` 和 `profiler.type` 控制 profiler 占位能力；第一批可使用 `dummy`。
- `patching.enabled` 控制是否启用 patching。

## Effective Config 生成规则

后端接收 run request 后，按固定顺序生成 effective config：

1. 校验 `schema_version`，拒绝未知版本或 schema 变体。
2. 根据 `settings_ref.use_saved_settings` 读取已保存 SSH / LLM 设置。
3. 根据 `sample.source_type` 物化 sample，并确认 `sample.entry_file` 存在。
4. 将 `commands` 映射为实际 build、correctness、benchmark 执行命令。
5. 根据 `target.gpu_model` 和 `target.backend` 补全硬件参数。
6. 应用 `hardware_overrides.fields`，并把覆盖来源标记为用户覆盖。
7. 写出脱敏后的 effective config、运行日志和结果文件。

GPU 参数自动补全不是准确性保证。每个硬件字段都必须有来源和置信度；来源缺失或置信度不足时，应使用 unknown / null 或保守默认值，而不是伪装成确定事实。

## 脱敏规则

run request、effective config、日志和报告都不能保存明文 password、API key、token、私钥或私有 host。需要引用 secret 时，只能保存环境变量名、设置引用或脱敏占位符。

允许展示的内容包括命令、sample 入口文件、GPU 型号、搜索预算、硬件字段来源、置信度、结果指标和失败原因。

## 结果语义

V2 仍然只返回当前预算内实际测到的 best-seen kernel，不保证全局最优。搜索预算、命令质量、硬件字段来源和 profiler 类型都会影响 best-seen 的可信度。
