# V2 User Flow

V2 第一批 run request 契约固定为 `schema_version: v2.run_request.v1`。后端、前端、文档都必须严格使用同一套 schema，不能各写变体。

V2 的用户体验目标是减少手写配置：用户在前端输入 sample 和 GPU 型号，补齐必要命令后创建任务；后端根据已保存设置和自动补全信息生成 effective config。

## 第一次设置 SSH / LLM

用户先在设置页保存远程 SSH 和 LLM 配置。前端可以把设置下载为 `settings.yaml`；run request 里只引用设置，不写明文密码、API key、token 或私钥内容。

V2 第一批 schema 使用：

```yaml
settings_ref:
  use_saved_settings: true
```

当 `use_saved_settings` 为 `true` 时，后端从 `settings.yaml` 读取 SSH / LLM 连接信息，并在生成 effective config 时使用环境变量名。run request、settings、日志和结果文件都不能保存明文 secret。

`settings.yaml` 只保存环境变量名和非密钥连接信息：

```yaml
schema_version: v2.user_settings.v1

runner:
  type: ssh

remote:
  host: your-ssh-host
  port: 22
  username: your-user
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /tmp/kernel_opt_workspace

llm:
  provider: openai_compatible
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY
```

用户需要在运行后端 CLI 的 shell 里自行设置真实 secret。

Linux / macOS bash：

```bash
export KERNEL_AGENT_SSH_PASSWORD='your-ssh-password'
export OPENAI_API_KEY='your-llm-api-key'
```

Windows cmd.exe：

```cmd
set KERNEL_AGENT_SSH_PASSWORD=your-ssh-password
set OPENAI_API_KEY=your-llm-api-key
```

Windows PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = 'your-ssh-password'
$env:OPENAI_API_KEY = 'your-llm-api-key'
```

## 新建优化任务

前端只能生成下面这一套 run request：

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

前端输入和 schema 字段一一对应：

- 任务名写入 `project_name`。
- sample 输入方式写入 `sample.source_type`，只能是 `inline`、`path` 或 `upload`。
- 代码文本写入 `sample.inline_text`；本地或上传路径写入 `sample.path`；入口文件写入 `sample.entry_file`。
- build、correctness、benchmark 三条命令分别写入 `commands.build_command`、`commands.correctness_command`、`commands.benchmark_command`。
- GPU 型号写入 `target.gpu_model`，后端类型写入 `target.backend`。
- 搜索预算写入 `search.max_iterations`、`search.candidates_per_iteration` 和 `search.timeout_seconds`。

前端只生成并下载文件，不直接执行后端，也不调用后端 HTTP API。真实执行命令是：

```bash
python main.py --run-request run_request.yaml --settings settings.yaml
```

## GPU 参数自动补全

用户输入 `target.gpu_model` 后，前端或后端可以尝试补全硬件参数，用于生成 effective config。自动补全不是准确性保证，不能把推测值当作官方事实。

每个自动补全字段都必须带来源和置信度，例如来自用户输入、硬件探测、profile、文档映射或保守默认值。来源缺失或置信度不足时，应保留 unknown / null，并在结果里可审计地展示。

## 用户覆盖硬件参数

用户可以通过 `hardware_overrides.fields` 覆盖硬件字段。覆盖值应在 effective config 中标记为用户覆盖，并保留字段来源与置信度，方便后续复查。

run request 仍然只保留：

```yaml
hardware_overrides:
  fields: {}
```

具体字段结构由 effective config 记录，不能另造 run request schema 变体。

## 查看结果

任务完成后，结果写入：

```text
kernel_opt_agent/workspace/results/
```

前端读取这个目录，展示当前预算内实际测到的 best-seen kernel、候选运行状态、失败原因、指标、日志路径和脱敏后的 effective config。

V2 仍然只返回 best-seen，不保证全局最优。用户需要根据预算、命令正确性、硬件字段来源和置信度判断结果是否足够可信。

当前限制：

- V1 / V2 都不保证全局最优，只返回当前预算内实际测到的 best-seen。
- profiler 不可用或指标缺失时，系统退化为 benchmark / log based analysis，缺失指标必须保持 `null`。
- 前端暂不提供 HTTP API；真实执行入口仍是后端 CLI。
- 远程 build、correctness、benchmark 命令必须受 `kernel_opt_agent/runner/command_guard.py` 约束。
