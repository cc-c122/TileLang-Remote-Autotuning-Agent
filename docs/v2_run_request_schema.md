# V2 Run Request Schema

V2 run request 是前端提交给后端的任务描述。普通用户不需要手写 `config.yaml`；后端根据 run request、长期设置和自动探测结果生成脱敏 `effective_config.yaml`。

## Run Request 字段

建议结构：

```yaml
project_name: my-tilelang-task

sample:
  source_type: upload
  path: <local-or-uploaded-sample-path>
  entry_file: kernel.py

commands:
  correctness_command: python correctness.py
  build_command: python -m py_compile kernel.py
  benchmark_command: python benchmark.py

target:
  gpu_name: <GPU_MODEL>
  backend: unknown

search:
  strategy: hybrid
  max_iterations: 5
  candidates_per_iteration: 4
  timeout_seconds: 300
  objective: latency

search_space:
  BM: [16, 32, 64]
  BN: [32, 64]
  BK: [32, 64]

patching:
  enabled: true
  max_patch_rounds: 5
  allowed_regions: [load, compute, store, layout]

profiler:
  enabled: true
  type: dummy

hardware_overrides:
  fields: {}
```

字段说明：

- `project_name`：任务名，用于结果目录和报告展示。
- `sample.source_type`：`upload`、`path` 或未来可扩展来源。
- `sample.path`：前端上传后或本地可访问的 sample 路径。
- `sample.entry_file`：允许模板渲染和 patch 的入口文件。
- `commands.correctness_command`：正确性检查命令。
- `commands.build_command`：构建命令，可为空。
- `commands.benchmark_command`：benchmark 命令。
- `target.gpu_name`：用户声明的 GPU 型号。
- `target.backend`：可选 backend，例如 `cuda`、`mxmaca`、`generic`、`unknown`。
- `search`：V1 参数搜索预算。
- `search_space`：参数候选空间。
- `patching`：V2 证据驱动 patch 预算和允许区域。
- `profiler`：profiler 开关和类型。
- `hardware_overrides.fields`：用户覆盖的硬件字段。

SSH 和 LLM 长期设置不建议每次放进 run request；它们由设置页提供引用，并在生成 effective config 时合并。

## Effective Config 生成规则

后端按以下顺序生成 `effective_config.yaml`：

1. 读取长期设置：SSH、remote workspace、LLM provider、LLM model、环境变量名。
2. 读取 run request：sample、commands、target GPU、search、patching、profiler。
3. 校验 sample 和 `entry_file`。
4. 校验 `search_space` 和模板占位符。
5. 根据目标 GPU 生成 hardware info 初稿。
6. 执行远程自动探测，如可用。
7. 加载内置 hardware profile。
8. 如果用户允许，读取官方文档缓存或用户提供文档。
9. 如仍缺关键字段，执行 safe probe。
10. 合并用户硬件覆盖，用户覆盖优先级最高。
11. 写出脱敏 `effective_config.yaml`。
12. 开始执行 V1/V2 优化流程。

硬件字段合并优先级：

1. `user_override` / `user_config`
2. `remote_detection`
3. `builtin_profile`
4. `doc_lookup`
5. `safe_probe`
6. `unknown`

GPU 参数自动补全不是准确性保证。所有硬件字段都必须记录：

- `value`
- `source`
- `confidence`
- `notes`

没有可靠来源的字段必须保持 `unknown` 或 `null`。safe probe 不能被描述为官方硬件理论上限。

## 脱敏规则

`effective_config.yaml` 可以包含：

- SSH host、port、username。
- SSH password 的环境变量名。
- LLM API key 的环境变量名。
- remote workspace。
- sample 路径。
- search / patch / profiler 配置。
- hardware 字段的 value/source/confidence/notes。

`effective_config.yaml` 不得包含：

- SSH password 明文。
- LLM API key 明文。
- token 明文。
- 私钥内容。
- 任意 secret 的完整值。

日志、JSONL、CSV、report、前端展示和 PR / issue 评论同样不得包含明文 secret。

推荐脱敏策略：

1. secret 字段只保存环境变量名。
2. `remote.key_path` 如需保留，应显示为 `<redacted:key_path>`。
3. 任何异常消息写入结果前都要过滤 password、token、API key。
4. 前端只显示“已配置/未配置”，不显示完整 secret。
5. 生成 report 时只引用脱敏后的 effective config。

## Best-Seen 语义

V2 仍然只返回当前搜索预算和 patch 预算内实际验证过的 best-seen kernel。自动补全硬件参数、profiler 诊断和 LLM patch 建议都不能被解释为全局最优保证。
