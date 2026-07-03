# TileLang Remote Autotuning Agent

TileLang Remote Autotuning Agent 是一个面向 TileLang kernel sample 的自动调参工具。它读取用户提供的算子样例、正确性检查命令、benchmark 命令和参数搜索空间，在本地 mock 环境或远程 SSH 容器中批量生成候选 kernel，逐个执行 correctness、build、benchmark，并把当前搜索预算内表现最好的版本保存下来。

这个项目的核心目标不是承诺找到全局最优解，而是提供一个安全、可复现、可审计的调参闭环：每个候选怎么生成、跑了什么命令、为什么失败、性能指标是多少、最终 best-seen kernel 来自哪组参数，都能在结果文件里复查。

## 项目能做什么

- 从 `config.yaml` 读取 kernel、runner、搜索策略、指标解析规则和安全约束。
- 支持 `local` runner，用 mock sample 在没有 GPU 和 SSH 的机器上跑通完整流程。
- 支持 `ssh` runner，通过 SSH password auth 或可选 SSH key auth 把 sample 上传到远程 workspace 执行。
- 用 `{{BM}}`、`{{BN}}`、`{{NUM_THREADS}}` 这类模板占位符生成候选 kernel。
- 每个候选先跑 correctness，失败的候选不会进入性能排名。
- benchmark 通过后解析 latency、TFLOPS、bandwidth。
- 支持 `grid`、`random`、`rule_based`、`llm`、`hybrid` 搜索策略。
- LLM 只负责建议参数组合，不能执行命令，也不能自由改写整个 kernel 文件。
- 保存 JSONL、CSV、日志、patch、best kernel、best config 和 Markdown 报告。
- 支持硬件探测和 safe probe，并在报告和前端中标注来源与置信度。
- 提供只读前端查看器，用于浏览 `workspace/results/` 下的运行产物。

## 不做什么

- 不保证全局最优，只返回当前搜索预算内实际测到的 best-seen 结果。
- 不让 LLM 生成或执行 shell command。
- 不让 LLM 自由重写完整 kernel 文件，只允许模板参数替换。
- 不安装系统包，不修改远程系统环境，不执行高风险系统命令。
- 不允许在配置文件、日志或结果文件中保存明文 SSH 密码；远程运行默认使用 password auth，密码必须通过环境变量传入。
- 前端 V1 不提供 HTTP API，也不会触发后端命令执行。

## 快速开始：本地 mock 流程

本地 mock 模式不需要 GPU，也不需要 SSH。它适合先验证主流程、配置格式、结果生成和前端展示。

```bash
pip install -r kernel_opt_agent/requirements.txt
python main.py --config kernel_opt_agent/config.example.yaml
```

运行测试：

```bash
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
```

运行完成后，结果会写入：

```text
kernel_opt_agent/workspace/results/
```

## 安装包使用方式

本地开发安装：

```bash
pip install -e .
```

wheel 安装：

```bash
pip install dist/*.whl
```

CLI 使用：

```bash
tilelang-agent --config config.yaml
```

## 配置文件怎么写

可以从示例配置开始：

- `kernel_opt_agent/config.example.yaml`：本地 mock 示例。
- `kernel_opt_agent/config.ssh.example.yaml`：SSH 远程运行示例。

最小配置需要包含这些部分：

```yaml
runner:
  type: local

kernel:
  sample_path: ./samples/mock
  entry_file: kernel.py
  build_command: python -m py_compile kernel.py
  correctness_command: python correctness.py
  run_command: python benchmark.py

search:
  strategy: hybrid
  max_iterations: 2
  candidates_per_iteration: 3
  timeout_seconds: 60
  objective: latency

search_space:
  BM: [16, 32, 64]
  BN: [32, 64]
  BK: [32, 64]
  NUM_THREADS: [128, 256]
  NUM_STAGES: [2, 3]
  VECTOR_WIDTH: [1, 2, 4]
  UNROLL_FACTOR: [1, 2]
  USE_SHARED: [true, false]
  USE_DOUBLE_BUFFER: [true, false]
```

注意：`kernel.sample_path` 如果是相对路径，会按 `kernel_opt_agent/` 目录解析。示例里的 `./samples/mock` 实际指向 `kernel_opt_agent/samples/mock`。

关键规则：

- `runner.type` 只能是 `local` 或 `ssh`。
- `search.strategy` 只能是 `grid`、`random`、`rule_based`、`llm`、`hybrid`。
- `search.objective` 只能是 `latency` 或 `tflops`。
- `search_space` 必填，且每个 key 都必须有非空候选值列表。
- 模板占位符必须和 `search_space` key 完全一致，包括大小写。
- boolean 参数渲染到 Python 文件时会变成 `True` / `False`。
- 配置文件不能直接写 API key、password、token。

## 模板参数怎么工作

V1 只渲染 `kernel.entry_file` 指向的入口文件。如果 sample 是目录，目录里的其他文件会原样复制和上传。

入口文件中写模板占位符：

```python
BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
num_threads = {{NUM_THREADS}}
num_stages = {{NUM_STAGES}}
vector_width = {{VECTOR_WIDTH}}
unroll_factor = {{UNROLL_FACTOR}}
use_shared = {{USE_SHARED}}
use_double_buffer = {{USE_DOUBLE_BUFFER}}
```

配置文件中必须声明同名搜索空间：

```yaml
search_space:
  BM: [16, 32, 64]
  BN: [32, 64]
  BK: [32, 64]
  NUM_THREADS: [128, 256]
  NUM_STAGES: [2, 3]
  VECTOR_WIDTH: [1, 2, 4]
  UNROLL_FACTOR: [1, 2]
  USE_SHARED: [true, false]
  USE_DOUBLE_BUFFER: [true, false]
```

如果模板里出现了未声明的占位符，或者 `search_space` 里有模板没有用到的参数，程序会直接报错，避免跑出不可复现的实验。

## correctness 和 benchmark 输出格式

推荐让 correctness 命令输出强约定格式：

```text
CORRECTNESS_RESULT status=<PASS|FAIL> max_error=<float> reason="<text>"
```

示例：

```text
CORRECTNESS_RESULT status=PASS max_error=0.00001 reason="ok"
```

推荐让 benchmark 命令输出强约定格式：

```text
BENCHMARK_RESULT latency_ms=<float> tflops=<float> bandwidth_gbps=<float>
```

示例：

```text
BENCHMARK_RESULT latency_ms=1.23 tflops=120.5 bandwidth_gbps=850.0
```

如果 benchmark 没有强格式，程序会尝试使用 `metrics.latency_regex`、`metrics.tflops_regex`、`metrics.bandwidth_regex` 做兼容解析。解析失败会记录为失败 case，不会中断整个搜索流程。

## 搜索策略

- `grid`：按 `search_space` 确定性枚举。
- `random`：使用固定 seed 从 `search_space` 采样。
- `rule_based`：用保守启发式选择小、中、大配置。
- `llm`：调用 OpenAI-compatible Chat Completions API 生成候选，但输出必须是严格 JSON，且必须通过 `search_space` 校验。
- `hybrid`：默认推荐。先尝试 LLM，失败时回退到 rule-based，再用 grid/random 补足候选数。

无论使用哪种策略，候选参数都只能来自 `search_space`。

## SSH 远程运行

复制 SSH 示例配置，不要提交真实配置：

```bash
cp kernel_opt_agent/config.ssh.example.yaml ssh.config.yaml
```

填写这些字段：

- `remote.host`
- `remote.port`
- `remote.username`
- `remote.auth_type: password`
- `remote.password_env`
- `remote.remote_workspace`

密码必须通过环境变量传入，不允许写入 `ssh.config.yaml`：

```bash
export KERNEL_AGENT_SSH_PASSWORD='your-password'
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = 'your-password'
```

推荐的远程认证配置：

```yaml
runner:
  type: ssh

remote:
  host: your-ssh-host
  port: 22
  username: your-user
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /tmp/kernel_opt_workspace
```

运行：

```bash
python main.py --config ssh.config.yaml
```

SSH runner 会把 sample 上传到远程 workspace，只渲染 `kernel.entry_file`，并在 `remote.remote_workspace` 下执行 correctness、build、benchmark。远程产物会拉回到本地结果目录中。

V1 必须支持 SSH password authentication。SSH key authentication 可作为兼容方式保留；如果使用 key auth，配置 `auth_type: key` 和 `remote.key_path`，但不要把私钥内容写入配置文件。

## 模力方舟容器连接说明

在模力方舟容器中运行远程调参时，先在控制台创建或启动带 TileLang/mcTileLang 运行环境的容器，并确认容器提供 SSH 连接信息。通常需要记录：

- SSH host
- SSH port
- username
- 登录密码
- 容器内用于调参的绝对路径，例如 `/root/kernel_opt_workspace` 或 `/tmp/kernel_opt_workspace`

本项目推荐使用 password auth 连接模力方舟容器，但密码只能放在本机环境变量中：

```bash
export KERNEL_AGENT_SSH_PASSWORD='your-model-ark-container-password'
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = 'your-model-ark-container-password'
```

配置示例：

```yaml
runner:
  type: ssh

remote:
  host: <模力方舟 SSH Host>
  port: <模力方舟 SSH Port>
  username: <容器用户名>
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /root/kernel_opt_workspace
```

确认 `remote.remote_workspace` 是容器内绝对路径，并且当前用户有写权限。`build_command`、`correctness_command` 和 `run_command` 会默认在这个 workspace 下执行；如果 sample 的 benchmark 需要进入子目录，请在命令中显式 `cd`，但仍会经过 command guard 检查。

## 硬件探测与 safe probe

V1 支持硬件自动探测和 safe probe，用于给搜索策略提供保守边界。相关结果会写入：

```text
kernel_opt_agent/workspace/results/hardware_detected.yaml
kernel_opt_agent/workspace/results/hardware_detection.log
kernel_opt_agent/workspace/results/hardware_probe.jsonl
```

需要特别注意：safe probe 是低/中置信度的可用性试探，不是官方硬件上限。`local_mock` 不验证真实 GPU 能力；`skipped` 只表示 probe 没有执行或没有得到真实结论，不表示硬件不支持。

`hardware_probe.jsonl` 当前字段包括：

- `probe_name`
- `param_name`
- `candidate_value`
- `status`
- `inference`
- `source`
- `confidence`
- `stdout_path`
- `stderr_path`

`status` 允许值：

- `pass`
- `failed`
- `timeout`
- `guard_denied`
- `exception`
- `skipped`

报告和前端会展示这些状态、置信度和日志路径，方便复查。

## 输出文件

主结果目录：

```text
kernel_opt_agent/workspace/results/
```

常见产物：

- `experiments.jsonl`：每个 trial 的完整结构化记录。
- `summary.csv`：简化摘要，便于浏览。
- `failed_cases.jsonl`：失败候选记录。
- `all_results.csv`：最终结果表。
- `best_kernel.py`：当前搜索预算内的 best-seen kernel。
- `best_config.yaml`：best-seen 参数配置。
- `report.md`：最终 Markdown 报告。
- `logs/`：每个 trial 的 stdout/stderr。
- `agent.log`：agent 运行日志。
- `effective_config.yaml`：脱敏后的实际配置。
- `hardware_detected.yaml`：硬件字段、来源和置信度。
- `hardware_probe.jsonl`：safe probe 记录。

这些运行产物默认不应提交到 Git。

## 前端结果查看器

前端文件：

```text
kernel_opt_agent/frontend/index.html
```

它是只读查看器，不调用后端 HTTP API，不执行 shell 命令，不读取 SSH key 或 API key，也不修改结果文件。

使用方式：

1. 在 Chromium 浏览器中打开 `kernel_opt_agent/frontend/index.html`，选择 `kernel_opt_agent/workspace/results/`。
2. 或用任意静态服务器服务 `kernel_opt_agent/`，打开 `/frontend/index.html`，页面会读取 `../workspace/results/`。

前端会展示 baseline、best-seen、提升比例、trial 状态、失败原因、日志路径、patch 路径、报告内容，以及硬件探测和 safe probe 摘要。后端还在运行时，缺失文件会显示为 waiting。

## 安全约束

命令安全：

- 用户配置的 correctness、build、benchmark 命令默认都在 workspace 根目录执行。
- V1 不支持给每条命令单独配置 working directory。
- 所有命令都会经过 `runner/command_guard.py` 检查。
- 默认禁止 `rm -rf`、`mkfs`、`dd if=`、`shutdown`、`reboot`、`apt remove`、`apt purge`、`yum remove`、`curl | sh`、`wget | sh` 等高风险片段。
- 禁止明显跳出 workspace 的破坏性操作。

敏感信息安全：

- LLM API key 只能通过 `llm.api_key_env` 指定的环境变量读取。
- SSH key 路径在脱敏配置中会被隐藏。
- API key、SSH password、token 不允许写入配置、日志、JSONL、CSV 或报告。

LLM 安全：

- LLM 输出必须是严格 JSON。
- LLM 推荐参数必须来自 `search_space`。
- LLM 不允许生成 shell command。
- LLM 调用失败时会 fallback，不会中断整个调参流程。

## 代码结构

```text
.
  README.md
  instruction.md
  main.py
  tests/
  kernel_opt_agent/
    config_model.py
    main.py
    agent/
    benchmark/
    frontend/
    hardware/
    hardware_profiles/
    kernel/
    profiler/
    runner/
    samples/mock/
    storage/
    workspace/results/
```

主要模块：

- `kernel_opt_agent/config_model.py`：读取和校验 YAML 配置。
- `kernel_opt_agent/main.py`：调度完整搜索流程。
- `kernel_opt_agent/runner/local_runner.py`：本地 mock/demo/test runner。
- `kernel_opt_agent/runner/ssh_runner.py`：SSH/SFTP 远程 runner。
- `kernel_opt_agent/runner/command_guard.py`：命令安全检查。
- `kernel_opt_agent/kernel/template_manager.py`：模板占位符解析与替换。
- `kernel_opt_agent/kernel/variant_generator.py`：候选 kernel 生成。
- `kernel_opt_agent/kernel/patch_manager.py`：保存候选 patch。
- `kernel_opt_agent/benchmark/correctness.py`：解析 correctness 输出。
- `kernel_opt_agent/benchmark/parser.py`：解析 benchmark 指标。
- `kernel_opt_agent/agent/optimizer_policy.py`：搜索策略。
- `kernel_opt_agent/agent/planner.py`：LLM 候选规划。
- `kernel_opt_agent/hardware/`：硬件探测、profile 合并和 safe probe。
- `kernel_opt_agent/storage/experiment_db.py`：写 JSONL、CSV 和日志。
- `kernel_opt_agent/storage/report_writer.py`：生成报告和 best kernel。
- `kernel_opt_agent/frontend/index.html`：只读结果查看器。

## 开发和 PR 检查

提交 PR 前至少运行：

```bash
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
python main.py --config kernel_opt_agent/config.example.yaml
```

不要提交：

- 真实 `config.yaml`
- SSH key
- API key
- token
- password
- 本地实验日志
- `kernel_opt_agent/workspace/generated/`
- `kernel_opt_agent/workspace/patches/`
- `kernel_opt_agent/workspace/results/` 中的运行产物

## 当前状态和下一步

当前 V1 已具备 local/mock 主流程、SSH key-auth 路径、硬件探测与 safe probe 结果展示、LLM fallback、安全命令检查、结构化结果记录和只读前端查看器。

仍需要继续推进：

1. 在真实 SSH 容器上完成更多 smoke test。
2. 用真实远程结果继续验证前端展示。
3. 完善 V1 release checklist 和 troubleshooting。
4. 后续实现更完整的 mxmaca/metax 真实 probe。
