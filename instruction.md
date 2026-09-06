# TileLang Remote Autotuning Agent 项目说明书

> 当前 V2 方向以第 21 节“算子源码优化与新架构适配”为准：不再建设独立参数搜索产品。第 1 至 20 节保留 V1 历史设计与兼容约束，不能据此要求新 Web 用户填写 search_space、模板占位符或配置文件。未冲突的正确性、安全、证据与可复现要求继续有效。

## 1. 项目定位

TileLang Remote Autotuning Agent 是一个面向 TileLang kernel sample 的远程自动调参智能体。第一版目标是实现一个可运行、可复现、可扩展的自动调参闭环，而不是承诺找到全局最优解。

系统接收用户提供的初始 TileLang kernel sample、benchmark 命令、correctness check 命令、远程算力容器 SSH 信息和 LLM API Key。系统在安全约束内将代码上传到远程 workspace，执行正确性检查、编译与 benchmark，解析性能指标，记录失败和日志，生成下一批参数化候选版本，迭代搜索，并在预算范围内返回 best-seen kernel。

本项目第一版必须具备真实执行能力，不能只保留空架子或伪代码。

## 2. 第一版目标与非目标

### 2.0 已确认项目规范

以下规范已经确认，后续后端线程和前端线程应按此执行：

1. 参数占位符统一使用 `{{BM}}` 形式的模板语法。
2. sample 支持文件或目录；V1 只渲染其中一个 `entry_file`，目录内其他文件原样上传。
3. benchmark 与 correctness 输出采用强约定格式，解析器按约定格式优先解析。
4. local runner 作为正式能力保留，用于 mock、demo 和 test 模式。
5. 前端 V1 只读取结果文件，不提供后端 HTTP API。
6. `build_command`、`correctness_command`、`run_command` 默认都在 workspace 根目录执行；V1 不支持每条命令单独配置 working directory。
7. SSH Runner V1 必须完整支持 password authentication；SSH key authentication 可作为兼容方式保留。
8. V1 必须要求用户在 `config.yaml` 中显式声明 `search_space`，所有搜索策略只能从 `search_space` 中选择参数。
9. LLM Provider V1 只实现 OpenAI-compatible Chat Completions API。
10. V1 配置中加入 `search.strategy`，支持 `grid | random | llm | rule_based | hybrid`。
11. `search_space` 参数名必须和模板占位符完全一致，并且区分大小写。
12. boolean 参数渲染到 Python 文件时统一使用 Python 字面量 `True` / `False`。

### 2.1 第一版目标

1. 支持本地调度、local runner、远程 SSH 执行、远程 workspace 文件上传与结果拉回。
2. 支持通过配置文件描述 kernel、远程环境、搜索预算、指标解析规则和安全约束。
3. 支持每个候选版本先运行 correctness check，正确性失败的候选不得进入性能排名。
4. 支持 benchmark stdout 指标解析，包括 latency、TFLOPS、bandwidth。
5. 支持 grid search、random search、LLM-guided search 和 fallback rule-based search。
6. 支持模板参数替换式候选生成，第一版不允许 LLM 自由改写整个 kernel 文件。
7. 支持每次修改 kernel 前保存 patch 或 git diff，便于审计和回滚。
8. 支持所有实验结果持久化，生成 JSONL、CSV、best kernel、best config 和 Markdown 报告。
9. 支持异常隔离：编译失败、运行失败、解析失败、正确性失败都必须记录，不能中断整个搜索流程。
10. 支持 profiler 扩展接口，第一版使用 dummy profiler，mxmaca profiler 预留接口和 TODO。

### 2.2 第一版非目标

1. 不承诺全局最优，只返回当前搜索预算内的最优版本。
2. 不实现复杂 profiler 指标采集。
3. 不让 LLM 直接生成或执行 shell 命令。
4. 不让 LLM 自由改写完整 kernel 文件。
5. 不实现复杂前端。前端 V1 只消费本项目输出的结构化结果文件，不要求 HTTP API。
6. 不修改远程系统环境，不安装系统包，不执行高风险系统命令。

## 3. 用户工作流

1. 用户准备 TileLang kernel sample 和 benchmark/correctness 命令。
2. 用户复制 `config.example.yaml` 为 `config.yaml` 并填写远程 SSH、LLM、kernel 和 search 配置。
3. 用户通过环境变量提供 LLM API Key，避免写入配置和日志。
4. 用户运行：

```bash
python main.py --config config.yaml
```

5. Agent 上传 sample 到远程 workspace。
6. Agent 生成 baseline 配置并执行 correctness、build、benchmark。
7. Agent 按策略生成下一批候选配置，逐个执行并记录结果。
8. Agent 在预算耗尽或达到停止条件后生成报告与产物。
9. 用户查看 `workspace/results/` 下的结果文件。

## 3.1 模力方舟容器连接说明

当远程算力环境来自模力方舟容器时，用户应先在模力方舟控制台创建或启动目标容器，并确认容器内已经具备 TileLang/mcTileLang、Python、编译工具链和 benchmark/correctness 命令所需依赖。

用户需要从模力方舟控制台或容器详情页获取以下 SSH 信息：

1. SSH host。
2. SSH port。
3. username。
4. 登录 password。
5. 容器内用于调参的 workspace 绝对路径，例如 `/root/kernel_opt_workspace` 或 `/tmp/kernel_opt_workspace`。

V1 推荐使用 password auth 连接模力方舟容器。password 不得写入 `config.yaml`，必须通过环境变量传入。例如：

```bash
export KERNEL_AGENT_SSH_PASSWORD='your-model-ark-container-password'
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = 'your-model-ark-container-password'
```

对应配置示例：

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

要求：

1. `remote.remote_workspace` 必须是容器内绝对路径，且当前用户必须有读写权限。
2. `build_command`、`correctness_command`、`run_command` 默认都在 `remote.remote_workspace` 下执行。
3. 如果 sample 的 benchmark 需要进入子目录，用户应在命令中显式写 `cd subdir && ...`，但命令仍必须通过 command guard。
4. 模力方舟 password 不得出现在配置、日志、JSONL、CSV、报告、前端展示或异常堆栈中。
5. 如果模力方舟容器同时支持 SSH key auth，可以保留 key auth 配置作为兼容路径，但 V1 主路径必须支持 password auth。

## 4. 技术栈

### 4.1 语言与运行环境

1. Python 3.10+
2. 操作系统兼容 Windows 本地调度与 Linux 远程容器执行。
3. 远程容器需具备 TileLang 运行环境、编译工具链和用户提供命令所需依赖。

### 4.2 主要依赖建议

1. `pydantic`：配置文件和 LLM JSON schema 校验。
2. `PyYAML`：读取 YAML 配置。
3. `paramiko`：SSH、SFTP 上传与下载。
4. Python 标准库：`argparse`、`logging`、`subprocess`、`json`、`csv`、`pathlib`、`re`、`time`、`datetime`、`random`、`dataclasses`。

依赖应尽量克制。第一版不引入复杂任务队列、Web 框架或数据库服务。

## 5. 项目目录结构

目标目录结构如下：

```text
kernel_opt_agent/
  README.md
  config.example.yaml
  main.py
  agent/
    planner.py
    llm_client.py
    optimizer_policy.py
    diagnosis.py
    prompt_templates.py
  runner/
    ssh_runner.py
    local_runner.py
    command_guard.py
  kernel/
    template_manager.py
    variant_generator.py
    patch_manager.py
  benchmark/
    parser.py
    correctness.py
    metrics.py
  profiler/
    base.py
    dummy_profiler.py
    mxmaca_profiler.py
  storage/
    experiment_db.py
    report_writer.py
  workspace/
    results/
```

说明：

1. `main.py` 是 CLI 入口，负责读取配置、初始化组件、运行搜索循环。
2. `agent/` 负责规划、诊断、LLM 交互和搜索策略。
3. `runner/` 负责本地或远程命令执行，所有命令必须经过 `command_guard.py`。
4. `kernel/` 负责模板参数替换、候选生成和 patch 保存。
5. `benchmark/` 负责 correctness 与性能指标解析。
6. `profiler/` 保留 profiler 扩展接口。
7. `storage/` 负责实验记录和最终报告。
8. `workspace/results/` 存放运行产物，不应存放密钥。

## 6. 配置文件规范

`config.yaml` 必须支持以下字段：

```yaml
project_name: tilelang-autotune-demo

runner:
  type: ssh

remote:
  host: 127.0.0.1
  port: 22
  username: user
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /workspace/tilelang_autotune

llm:
  provider: openai_compatible
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  model: gpt-4o-mini
  temperature: 0.2
  max_tokens: 2048

kernel:
  sample_path: ./samples/
  entry_file: kernel.py
  build_command: python -m py_compile kernel.py
  run_command: python benchmark.py
  correctness_command: python correctness.py

search:
  strategy: hybrid
  max_iterations: 5
  candidates_per_iteration: 4
  timeout_seconds: 300
  objective: latency

search_space:
  BM: [16, 32, 64, 128]
  BN: [32, 64, 128]
  BK: [32, 64]
  NUM_THREADS: [128, 256]
  NUM_STAGES: [2, 3, 4]
  VECTOR_WIDTH: [1, 2, 4]
  UNROLL_FACTOR: [1, 2, 4]
  USE_SHARED: [true, false]
  USE_DOUBLE_BUFFER: [true, false]

metrics:
  latency_regex: "latency\\s*[:=]\\s*([0-9.]+)"
  tflops_regex: "tflops\\s*[:=]\\s*([0-9.]+)"
  bandwidth_regex: "bandwidth\\s*[:=]\\s*([0-9.]+)"

constraints:
  max_shared_memory: 98304
  max_registers: 255
  allowed_file_patterns:
    - "*.py"
    - "*.yaml"
    - "*.json"
    - "*.txt"
  denied_commands:
    - "rm -rf"
    - "apt remove"
    - "mkfs"
    - "dd if="
```

配置校验要求：

1. `runner.type` 只能是 `ssh` 或 `local`。
2. `remote.remote_workspace` 必须是绝对路径。
3. `search.objective` 只能是 `latency` 或 `tflops`。
4. `remote.auth_type` 只能是 `password` 或 `key`。
5. `remote.auth_type=password` 时必须提供 `remote.password_env`，V1 必须完整支持 password auth，并且只允许从该环境变量读取密码。
6. `remote.auth_type=key` 时必须提供 `remote.key_path`，key auth 可作为兼容认证方式保留。
7. 不得在 `config.yaml` 中直接写明文 password；配置模型、日志、JSONL、CSV、报告和异常堆栈都不得保存明文 password。
8. `llm.provider` V1 只能是 `openai_compatible`。
9. `llm.api_key_env` 只保存环境变量名，不直接保存 API Key。
10. `kernel.sample_path` 必须存在，可以是单个文件或 sample 目录。
11. 当 `sample_path` 是目录时，`kernel.entry_file` 必须是该目录下的相对路径；V1 只对 `entry_file` 做模板渲染，其他文件原样上传。
12. `timeout_seconds`、`max_iterations`、`candidates_per_iteration` 必须为正整数。
13. `search.strategy` 只能是 `grid`、`random`、`llm`、`rule_based`、`hybrid`，默认建议为 `hybrid`。
14. `search_space` 是 V1 必填字段，不能为空。
15. 每个模板占位符如果需要被调参，必须在 `search_space` 中声明。
16. `search_space` 参数名必须和模板占位符完全一致，并且区分大小写。
17. 如果模板里出现未替换占位符，程序必须报错。
18. boolean 参数渲染到 Python 文件时统一使用 Python 字面量 `True` / `False`。
19. `constraints.denied_commands` 必须合并默认高危命令列表，用户不能通过配置清空安全红线。

## 7. 安全规范

### 7.1 命令安全

所有远程命令必须经过 `runner/command_guard.py` 检查。检查规则至少包括：

1. 禁止执行 `rm -rf`、`mkfs`、`dd if=`、`shutdown`、`reboot`、`apt remove`、`apt purge`、`yum remove`、`curl | sh`、`wget | sh` 等高风险命令。
2. 禁止重定向覆盖系统路径，例如 `/etc/`、`/usr/`、`/bin/`、`/lib/`。
3. 禁止包含明显逃逸 workspace 的 destructive 操作。
4. 远程命令必须在指定 `remote_workspace` 下执行。
5. 每条命令必须设置 timeout。
6. 不能让 LLM 直接生成 shell 命令并执行。

命令执行目录语义：

1. 用户提供的 `build_command`、`correctness_command`、`run_command` 默认都在 workspace 根目录执行。
2. 对 SSH Runner，所有命令默认在 `remote.remote_workspace` 下执行。
3. V1 不支持每条命令单独配置 working directory。
4. 所有生成文件、日志、结果都应位于 workspace 内。
5. `command_guard` 必须保证命令不会跳出 workspace 执行危险操作。
6. 如果用户需要子目录执行，应在命令中自行写明，例如 `"cd samples/moe_gemm && python3 benchmark.py"`，但仍必须通过 command guard 检查。

示例：

```yaml
remote:
  remote_workspace: /root/kernel_opt_workspace

kernel:
  build_command: "python3 benchmark.py --build"
  correctness_command: "python3 correctness.py"
  run_command: "python3 benchmark.py"
```

实际远程执行语义等价于：

```bash
cd /root/kernel_opt_workspace && python3 benchmark.py --build
cd /root/kernel_opt_workspace && python3 correctness.py
cd /root/kernel_opt_workspace && python3 benchmark.py
```

### 7.2 敏感信息安全

1. LLM API Key、SSH 密码、访问令牌不得写入日志、JSONL、CSV、报告或异常堆栈。
2. 日志中出现环境变量值时必须脱敏。
3. `config.example.yaml` 不得包含真实密钥。
4. 运行时只允许通过环境变量或用户本地 SSH agent/密钥文件读取敏感信息。

### 7.3 文件安全

1. 上传文件必须匹配 `constraints.allowed_file_patterns`。
2. 拉回结果仅限远程 workspace 下的 `results/`、`logs/`、best kernel 等项目产物。
3. 每次候选 kernel 修改前必须保存 patch 或 git diff。
4. 不得自动修改用户原始 sample，候选版本应写入工作区副本。

## 8. 核心模块设计

### 8.1 SSH Runner

文件：`runner/ssh_runner.py`

职责：

1. 建立 SSH/SFTP 连接。
2. 上传本地 sample、辅助脚本和候选 kernel 到远程 workspace。
3. 在 `remote.remote_workspace` 根目录内执行 correctness、build、benchmark 命令。
4. 每条命令执行前调用 command guard。
5. 捕获 stdout、stderr、return code、start_time、end_time、duration、timeout。
6. 拉回 `results/`、`logs/`、`best_kernel.py` 等产物。
7. 连接失败、认证失败、命令超时等异常要转换成结构化结果，不得直接导致全局崩溃。
8. V1 必须完整支持 password authentication。
9. SSH key authentication 可作为兼容方式保留；若用户配置 `auth_type=key`，必须提供 `remote.key_path`。
10. password 必须通过 `password_env` 指定的环境变量读取，不允许直接写入 `config.yaml`。
11. password、token、API key 不得写入日志、JSONL、CSV、报告或异常堆栈。

认证配置示例：

```yaml
runner:
  type: ssh

remote:
  host: example.com
  port: 22
  username: root
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /root/kernel_opt_workspace
```

key auth 兼容配置：

```yaml
remote:
  auth_type: key
  key_path: ~/.ssh/id_rsa
```

要求：

1. V1 必须支持 `auth_type=password`。
2. 不允许把 password 直接写入 `config.yaml`，必须通过环境变量读取。
3. `password_env` 只保存环境变量名。
4. 程序读取密码后不得把密码写入日志、结果文件、报告或异常消息。
5. 如果 `auth_type=key`，key auth 可以继续使用，但不得影响 password auth 的主路径。

### 8.2 Command Guard

文件：`runner/command_guard.py`

职责：

1. 校验命令是否包含高危片段。
2. 校验命令是否在指定 workspace 中执行。
3. 提供 `validate(command, cwd, remote_workspace) -> GuardResult`。
4. Guard 失败时返回明确原因，记录到失败实验结果。

### 8.2.1 Local Runner

文件：`runner/local_runner.py`

local runner 是 V1 正式保留能力，不只是临时脚本。它用于 mock、demo 和 test 模式，使项目在没有远程 GPU/SSH 环境时也能跑通完整调参流程。

职责：

1. 在本地 workspace 中执行与 SSH runner 相同的 correctness、build、benchmark 流程。
2. 复用 command guard、timeout、stdout/stderr 捕获和结构化结果模型。
3. 支持 mock benchmark/correctness 示例，方便 CI 或开发机验证主流程。
4. 结果文件格式必须与 SSH runner 保持一致，前端和报告模块不应关心 runner 类型。

### 8.3 Benchmark Parser

文件：`benchmark/parser.py`

Benchmark stdout 强约定格式：

```text
BENCHMARK_RESULT latency_ms=<float> tflops=<float> bandwidth_gbps=<float>
```

示例：

```text
BENCHMARK_RESULT latency_ms=1.23 tflops=120.5 bandwidth_gbps=850.0
```

其中：

1. `latency_ms` 单位固定为毫秒。
2. `tflops` 为数值，单位固定为 TFLOPS。
3. `bandwidth_gbps` 为数值，单位固定为 GB/s。
4. 若某指标不可用，应输出 `nan` 或省略该字段；解析器需记录缺失原因。

职责：

1. 优先按强约定格式从 stdout 中解析 latency、TFLOPS、bandwidth。
2. 若强约定格式不存在，再根据配置中的 regex 兼容解析。
3. 解析失败时设置 `parse_error=true`，并保留原始 stdout/stderr。
4. 支持单位字段的后续扩展。
5. 不因单个指标缺失而崩溃；缺失字段填 `null` 并记录原因。

### 8.4 Correctness Check

文件：`benchmark/correctness.py`

Correctness stdout 强约定格式：

```text
CORRECTNESS_RESULT status=<PASS|FAIL> max_error=<float> reason="<text>"
```

示例：

```text
CORRECTNESS_RESULT status=PASS max_error=0.00001 reason="ok"
```

失败示例：

```text
CORRECTNESS_RESULT status=FAIL max_error=0.12 reason="max error exceeds tolerance"
```

其中：

1. `status` 只能是 `PASS` 或 `FAIL`。
2. `max_error` 为数值；无法计算时可输出 `nan`。
3. `reason` 应为短文本，不得包含密钥、token 或远程密码。

职责：

1. 每个候选必须先执行 correctness_command。
2. 优先解析强约定格式中的 status、max_error、reason。
3. 若 return code 非 0 或日志匹配失败关键词，则标记为 correctness failed。
4. 尽量兼容解析 max error、relative error、pass/fail 等字段。
5. 正确性失败的候选不得进入性能 ranking。
6. 正确性失败仍需写入 `failed_cases.jsonl` 和 `experiments.jsonl`。

### 8.5 Variant Generator

文件：`kernel/variant_generator.py`

第一版只支持模板参数替换，不允许 LLM 直接自由修改整个 kernel。

V1 所有可调参数必须来自 `config.yaml` 中显式声明的 `search_space`。`search_space` 是 grid search、random search、LLM-guided search 和 rule-based fallback 的共同边界，程序和 LLM 都不能凭空猜参数范围。

支持参数：

1. `BM`
2. `BN`
3. `BK`
4. `NUM_THREADS`
5. `NUM_STAGES`
6. `VECTOR_WIDTH`
7. `UNROLL_FACTOR`
8. `USE_SHARED`
9. `USE_DOUBLE_BUFFER`

模板必须采用显式占位符，例如：

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

替换要求：

1. 参数值必须来自 `search_space` 或 LLM JSON 输出中通过 schema 校验且属于 `search_space` 的候选。
2. V1 只渲染 `kernel.entry_file` 指向的文件；当 sample 是目录时，其他文件原样复制和上传。
3. 生成前后必须保存 patch。
4. 候选文件命名应包含 iteration 和 candidate id，例如 `kernel_iter002_cand003.py`。
5. 如果模板里出现未替换占位符，程序必须报错并拒绝启动或拒绝该候选。
6. 程序需要记录每次 trial 使用的完整参数配置。
7. `search_space` 参数名必须和模板占位符完全一致，并且区分大小写。
8. boolean 参数渲染到 Python 文件时统一使用 Python 字面量 `True` / `False`。

### 8.6 Patch Manager

文件：`kernel/patch_manager.py`

职责：

1. 为每个候选保存可审计 patch。
2. 保存 baseline 与候选版本的 diff。
3. patch 文件路径写入实验记录。
4. 支持后续回滚或重放实验。

### 8.7 Diagnosis

文件：`agent/diagnosis.py`

职责：

根据已有实验数据输出可能瓶颈，第一版只输出保守结论：

1. latency 高但 TFLOPS 低：可能计算单元利用率不足。
2. bandwidth 接近配置或估计上限：可能 memory-bound。
3. 编译日志出现 `private memory`、`local memory`：可能寄存器溢出或私有内存过多。
4. shared memory 占用过高：可能 occupancy 降低。
5. 小 tile 性能差：可能数据复用不足。
6. 大 tile 编译失败或性能下降：可能寄存器/shared memory 压力过大。
7. profiler 指标不可用时，报告必须使用“可能”“倾向于”“需要进一步验证”等表述，不得伪装成确定结论。

### 8.8 LLM Planner

文件：`agent/planner.py`、`agent/llm_client.py`、`agent/prompt_templates.py`

LLM Provider V1 只实现 OpenAI-compatible Chat Completions API。只要某个厂商提供 OpenAI-compatible endpoint，就可以通过 `base_url` 接入；V1 不兼容各厂商私有字段。

配置示例：

```yaml
llm:
  provider: openai_compatible
  base_url: "https://api.openai.com/v1"
  api_key_env: "OPENAI_API_KEY"
  model: "gpt-4o-mini"
  temperature: 0.2
  max_tokens: 2048
```

接口语义：

```text
POST {base_url}/chat/completions
```

请求体使用 OpenAI-compatible 格式：

```json
{
  "model": "...",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0.2
}
```

每轮输入：

1. baseline 性能。
2. 当前 top 5 配置。
3. 失败配置。
4. 最近一轮日志摘要。
5. 当前参数搜索空间。
6. 约束条件。

每轮输出严格 JSON：

```json
{
  "candidates": [
    {
      "config": {
        "BM": 64,
        "BN": 64,
        "BK": 32,
        "NUM_THREADS": 256,
        "NUM_STAGES": 3,
        "VECTOR_WIDTH": 4,
        "UNROLL_FACTOR": 2,
        "USE_SHARED": true,
        "USE_DOUBLE_BUFFER": false
      },
      "hypothesis": "Increase data reuse with moderate tile sizes.",
      "expected_improvement": "Lower latency by improving arithmetic intensity.",
      "risk": "May increase shared memory and reduce occupancy."
    }
  ]
}
```

要求：

1. 使用 pydantic 校验 LLM 输出 schema。
2. JSON 非法、字段缺失、类型错误、候选数量不匹配时，fallback 到 rule-based search。
3. LLM 不能输出 shell 命令。
4. LLM 输出不能包含密钥或日志中的敏感信息。
5. API key 必须通过环境变量读取。
6. 不允许把 API key 写入 `config.yaml`。
7. 不允许把 API key 打印到日志。
8. LLM 输出必须是严格 JSON。
9. 如果 LLM 调用失败，必须 fallback 到 grid/random/rule-based search。
10. LLM 只能从 `search_space` 给出的候选值中选择参数，不允许生成 `search_space` 外的参数。

### 8.9 Optimizer Policy

文件：`agent/optimizer_policy.py`

第一版策略：

1. `grid search`：按 `search_space` 确定性枚举。
2. `random search`：使用固定 seed，只在 `search_space` 中采样，保证可复现。
3. `LLM-guided search`：调用 planner 生成候选，但候选参数必须属于 `search_space`。
4. `fallback rule-based search`：当 LLM 不可用或输出非法时使用保守启发式，但也只能在 `search_space` 中选择参数。
5. `hybrid search`：默认推荐策略，优先使用 LLM-guided search；LLM 不可用或输出非法时 fallback 到 rule-based，再由 grid/random 补足候选数量。

配置字段：

```yaml
search:
  strategy: hybrid
```

`search.strategy` 支持 `grid | random | llm | rule_based | hybrid`。

去重要求：

1. 已尝试配置不得重复运行。
2. 配置 hash 必须写入实验记录。
3. 若候选重复，策略层应补充生成新候选。
4. 不允许任何策略生成 `search_space` 外的参数值。
5. 程序需要记录每次 trial 使用的完整参数配置。

### 8.10 Experiment DB

文件：`storage/experiment_db.py`

职责：

1. 追加写 `workspace/results/experiments.jsonl`。
2. 更新 `workspace/results/summary.csv`。
3. 保存 failed cases 到 `workspace/results/failed_cases.jsonl`。
4. 记录每个实验的完整元数据，包括：
   - run id
   - iteration
   - candidate id
   - config
   - config hash
   - correctness status
   - build status
   - run status
   - parsed metrics
   - objective value
   - stdout/stderr log path
   - patch path
   - error category
   - error message
   - timestamps

### 8.11 Report Writer

文件：`storage/report_writer.py`

最终生成：

1. `best_kernel.py`
2. `best_config.yaml`
3. `report.md`
4. `all_results.csv`
5. `failed_cases.jsonl`

`report.md` 必须包含：

1. baseline 性能。
2. best 性能。
3. 提升比例。
4. 最佳参数。
5. 每轮尝试了什么。
6. 哪些尝试失败。
7. 可能瓶颈分析。
8. 后续建议。
9. 明确说明结果是搜索预算内 best-seen，不代表全局最优。

### 8.12 Profiler 接口

文件：

```text
profiler/base.py
profiler/dummy_profiler.py
profiler/mxmaca_profiler.py
```

接口建议：

```python
class ProfilerResult:
    available: bool
    metrics: dict
    warnings: list[str]
    raw_output_path: str | None

class BaseProfiler:
    def collect(self, run_context) -> ProfilerResult:
        raise NotImplementedError
```

第一版：

1. `DummyProfiler` 返回 `available=False`。
2. `MxmacaProfiler` 保留 TODO 和接口，未来解析 warp 活跃度、私有内存、HBM 读写带宽、shared memory conflict 等指标。

## 9. 搜索流程

推荐主流程：

1. 加载并校验配置。
2. 初始化日志，创建本地 `workspace/results/`。
3. 读取 sample，生成 baseline 工作副本。
4. 建立 runner。
5. 上传 baseline 文件到远程 workspace。
6. 运行 baseline correctness、build、benchmark。
7. 写入 baseline 实验结果。
8. 对每一轮 iteration：
   - 根据策略生成候选配置。
   - 对每个候选生成 kernel 文件并保存 patch。
   - 上传候选文件。
   - 执行 correctness。
   - correctness 通过后执行 build。
   - build 通过后执行 benchmark。
   - 解析指标并写入实验记录。
   - 失败时记录失败类型并继续下一个候选。
   - 每轮结束更新 best-seen。
   - 根据已有结果生成诊断摘要。
9. 预算结束后生成最终报告。
10. 拉回远程结果与日志。

## 10. 结果文件规范

### 10.1 `experiments.jsonl`

每行一个实验记录，建议结构：

```json
{
  "run_id": "20260701-153000",
  "iteration": 1,
  "candidate_id": 2,
  "config": {"BM": 64, "BN": 64},
  "config_hash": "abc123",
  "status": "benchmark_ok",
  "correctness": {"passed": true, "max_error": 0.0001},
  "metrics": {"latency": 1.23, "tflops": 120.5, "bandwidth": null},
  "objective": {"name": "latency", "value": 1.23, "better": "lower"},
  "paths": {
    "kernel": "workspace/generated/kernel_iter001_cand002.py",
    "patch": "workspace/patches/kernel_iter001_cand002.patch",
    "stdout": "workspace/results/logs/iter001_cand002.stdout.log",
    "stderr": "workspace/results/logs/iter001_cand002.stderr.log"
  },
  "error": null
}
```

### 10.2 `summary.csv`

面向快速查看，至少包含：

```text
run_id,iteration,candidate_id,status,latency,tflops,bandwidth,objective_value,config_hash,kernel_path,patch_path
```

### 10.3 `failed_cases.jsonl`

记录所有失败候选，包括：

1. correctness failure
2. build failure
3. run timeout
4. run nonzero exit
5. parse error
6. guard denied
7. upload/download failure
8. LLM invalid JSON

## 11. 日志规范

1. 使用 Python `logging` 模块。
2. 默认日志级别为 `INFO`。
3. 详细 stdout/stderr 写入独立日志文件，主日志只写路径和摘要。
4. 所有异常必须捕获并写入结构化结果。
5. 日志不得包含密钥、密码、token。
6. 命令日志应记录脱敏后的命令、cwd、timeout、return code、duration。

## 12. 可复现性规范

1. random search 必须使用固定 seed，seed 写入结果。
2. 每个候选 config 必须写入完整参数。
3. 每个候选 kernel 文件和 patch 必须保存。
4. 每次执行的 stdout/stderr 必须保存。
5. 每轮策略输入摘要和输出候选必须保存。
6. best result 应能通过 `best_kernel.py` 与 `best_config.yaml` 复现。

## 13. 开发规范

### 13.1 代码风格

1. Python 代码优先使用类型标注。
2. 函数应保持单一职责。
3. 不使用 `eval`。
4. 不把安全逻辑散落在业务代码中，统一走 `command_guard.py`。
5. 不把 LLM 结果直接拼成命令执行。
6. 模块间传输结构化对象，不依赖隐式全局状态。

### 13.2 错误处理

1. 单个候选失败不能中断整个任务。
2. Runner 失败要转为结构化状态。
3. Parser 失败要标记 `parse_error`。
4. LLM 失败要 fallback。
5. 最终报告必须包含失败案例摘要。

### 13.3 测试建议

第一版至少补充以下测试或可运行示例：

1. config schema 校验测试。
2. command guard 拒绝高危命令测试。
3. benchmark parser regex 解析测试。
4. correctness parser 测试。
5. variant generator 模板替换测试。
6. LLM 输出 JSON schema 校验测试。
7. 一个 local runner mock 示例，用于无远程 GPU 时跑通主流程。

## 14. 后端线程职责建议

后端线程优先实现可运行 MVP：

1. 配置模型与 CLI。
2. command guard。
3. local runner 与 ssh runner。
4. template manager、variant generator、patch manager。
5. correctness、parser、metrics。
6. experiment db 与 report writer。
7. grid/random/rule-based policy。
8. LLM client 与 planner fallback。
9. dummy profiler 与 mxmaca profiler 接口。
10. README 和 config.example.yaml。

后端验收标准：

1. 能用 mock/local sample 跑完整流程。
2. 能生成所有规定结果文件。
3. 失败候选不会中断流程。
4. 不泄露密钥。
5. 命令 guard 能拒绝危险命令。

## 15. 前端线程职责建议

如果前端线程参与，第一版只做结果文件查看器，不介入调参执行，也不要求后端提供 HTTP API：

1. 读取 `workspace/results/summary.csv` 和 `report.md`。
2. 可选读取 `workspace/results/experiments.jsonl`、`all_results.csv`、`failed_cases.jsonl`。
3. 展示 best-seen 配置、baseline/best 对比、迭代曲线、失败案例。
4. 展示每个候选的状态和日志路径。
5. 明确标注结果不是全局最优。
6. 不展示密钥，不支持直接执行任意命令。
7. V1 不设计 HTTP API，前端通过文件系统或用户指定结果目录读取产物。

前端可以后续演进为：

1. 配置文件生成器。
2. 实验进度面板。
3. kernel diff 查看器。
4. 远程任务监控 UI。

## 16. 验收标准

第一版完成时必须满足：

1. `python main.py --config config.yaml` 可启动。
2. 使用 local/mock runner 时可在无 GPU 环境跑通完整流程。
3. 使用 SSH 配置时可上传、执行、拉回结果。
4. 每个候选都执行 correctness check。
5. correctness/build/run/parse 失败均被记录且不终止全局流程。
6. `workspace/results/experiments.jsonl`、`summary.csv`、`failed_cases.jsonl` 可生成。
7. `best_kernel.py`、`best_config.yaml`、`report.md` 可生成。
8. LLM 输出非法时 fallback 到规则搜索。
9. 危险命令被 command guard 拒绝。
10. README 写清楚配置、运行、结果查看、安全限制和非全局最优声明。

## 17. 已确认架构决策与剩余问题

以下架构决策已经确认，后端线程和前端线程应直接按此实现：

1. `build_command`、`correctness_command`、`run_command` 默认都在 workspace 根目录执行；SSH Runner 默认在 `remote.remote_workspace` 下执行。
2. V1 不支持每条命令单独配置 working directory。
3. 所有生成文件、日志、结果都应位于 workspace 内。
4. V1 优先且必须完整支持 SSH password authentication。
5. SSH key authentication 可作为兼容方式保留；如果用户配置 `auth_type=key`，必须提供 `remote.key_path`。
6. 密码必须通过 `password_env` 指定的环境变量读取，不允许写入 `config.yaml`，也不得写入日志、JSONL、CSV、报告或异常堆栈。
7. V1 必须要求用户显式声明 `search_space`，且不能为空。
8. 所有搜索策略和 LLM Planner 都只能选择 `search_space` 内的参数值。
9. 如果模板里出现未替换占位符，程序必须报错。
10. V1 只实现 OpenAI-compatible Chat Completions API。
11. `search.strategy` 支持 `grid | random | llm | rule_based | hybrid`，默认建议为 `hybrid`。
12. `search_space` 参数名必须和模板占位符完全一致，并且区分大小写。
13. boolean 参数渲染到 Python 文件时统一使用 Python 字面量 `True` / `False`。

当前没有阻塞 V1 开发的开放问题。后续若后端或前端线程遇到实现歧义，应优先回到本说明书补充决策，再改代码。

## 18. 建议的实现优先级

1. 先完成 config、runner、guard、parser、experiment db，确保执行链路真实可跑。
2. 再完成 variant generator、patch manager、grid/random search。
3. 再接入 correctness 强制门禁和 report writer。
4. 再实现 LLM planner 与 schema fallback。
5. 最后补 README、示例 config、mock sample 和 profiler 预留接口。

第一版应该优先保证安全、可复现和失败隔离。性能搜索策略可以朴素，但记录必须完整，结果必须能复查。

## 19. V1 追加架构决策：Hardware Auto Detection and Safe Probing

算子优化与目标硬件强相关。V1 不要求用户必须完整填写 GPU 硬件参数，但系统必须支持硬件自动探测，并在探测失败时使用内置硬件 profile、文档缓存和安全试探补充信息。所有硬件字段都必须记录来源和置信度；无法确认的字段必须保持 `null` 或 `unknown`，不得为了搜索顺畅而伪造参数。

本章节是 V1 的追加架构决策。后续后端线程和前端线程应按本章节更新实现、报告和结果查看器。

### 19.1 目标

用户只需要声明目标硬件名称和远程环境。Agent 优先自动探测硬件能力；探测不到时查内置 profile 或官方文档缓存；仍无法确认时执行安全 probe；最终根据已知约束和 probe 结果进入正常搜索或保守搜索。

目标不是在 V1 准确复刻完整 GPU 规格表，而是：

1. 给搜索策略提供基本安全边界。
2. 避免 LLM 或规则搜索基于未知硬件参数过度激进。
3. 在报告中清楚说明哪些字段可信、哪些字段未知。
4. 让后续支持不同 backend 时有稳定接口。

### 19.2 配置新增字段

`config.yaml` 新增可选 `hardware` 与 `hardware_detection` 字段：

```yaml
hardware:
  target_name: metax-c500
  backend: mxmaca
  profile: metax_c500
  allow_doc_lookup: false
  doc_paths: []
  fields:
    total_memory_GB: null
    available_memory_GB: null
    warp_size: null
    wave_size: null
    max_threads_per_block: null
    shared_memory_per_block_bytes: null
    max_registers_per_thread: null
    vector_alignment_bytes: null
    supported_dtypes: []

hardware_detection:
  enabled: true
  remote_detection: true
  builtin_profile: true
  doc_lookup: false
  safe_probe: true
  conservative_unknown_mode: true
  timeout_seconds: 60
```

配置要求：

1. `hardware` 字段可选；用户显式填写的字段优先级最高。
2. `hardware.target_name` 是用户声明的目标硬件名称，可用于选择内置 profile。
3. `hardware.backend` 可选，常见值包括 `cuda`、`mxmaca`、`generic`、`unknown`。
4. `hardware.profile` 可选，用于指定 `hardware_profiles/*.yaml`。
5. `hardware_detection.enabled=false` 时跳过自动探测，但仍应生成 `hardware_detected.yaml`，并标明哪些字段来自 user config、哪些未知。
6. `hardware_detection.doc_lookup` V1 默认关闭；只有用户显式允许联网或提供文档路径时才能使用。
7. 所有探测、profile、probe 结果不得覆盖用户显式配置。

### 19.3 硬件信息来源优先级

硬件字段按以下优先级合并：

1. `user_config`：用户在 `config.yaml` 中显式填写的 `hardware` 字段。
2. `remote_detection`：通过 SSH runner 在远程 workspace 中执行安全探测命令获取。
3. `builtin_profile`：读取项目内置硬件 profile，例如 `hardware_profiles/metax_c500.yaml`。
4. `doc_lookup`：用户允许联网时查询官方文档，或读取用户提供的文档缓存。V1 可先实现为 TODO 或手动导入文档缓存。
5. `safe_probe`：关键字段仍未知时运行小型安全 probe。
6. `unknown`：仍无法确认的字段保持 `null` 或 `unknown`。

合并规则：

1. 用户显式配置永远覆盖自动探测结果。
2. 自动探测结果覆盖内置 profile。
3. 内置 profile 覆盖文档缓存。
4. 文档缓存覆盖 safe probe。
5. safe probe 只补充经验性可用/不可用结论，不得声明官方硬件理论上限。
6. 每个字段必须记录：
   - `value`
   - `source`
   - `confidence`
   - `notes`
7. `source` 只能使用 `user_config`、`remote_detection`、`builtin_profile`、`doc_lookup`、`safe_probe`、`unknown`。
8. `confidence` 建议使用 `high`、`medium`、`low`、`unknown`。

建议统一结构：

```yaml
fields:
  total_memory_GB:
    value: 64
    source: builtin_profile
    confidence: medium
    notes: "from built-in metax_c500 profile"
  shared_memory_per_block_bytes:
    value: null
    source: unknown
    confidence: unknown
    notes: "not detected; conservative mode enabled"
```

### 19.4 新增目录与模块

新增目录：

```text
hardware/
  __init__.py
  detector.py
  detection_commands.py
  cuda_detector.py
  mxmaca_detector.py
  generic_linux_detector.py
  profile_loader.py
  safe_probe.py
  hardware_info.py

hardware_profiles/
  metax_c500.yaml
  unknown_gpu.yaml
```

模块职责：

1. `hardware/hardware_info.py`：定义硬件字段、来源、置信度和合并后的结构化对象。
2. `hardware/detector.py`：统一调度 remote detection、profile loading、doc lookup 和 safe probe。
3. `hardware/detection_commands.py`：维护 backend 探测命令白名单。
4. `hardware/cuda_detector.py`：CUDA/NVIDIA 环境探测，使用安全只读命令。
5. `hardware/mxmaca_detector.py`：MetaX/MACA 环境探测，使用安全只读命令。
6. `hardware/generic_linux_detector.py`：通用 Linux 只读探测。
7. `hardware/profile_loader.py`：读取和校验 `hardware_profiles/*.yaml`。
8. `hardware/safe_probe.py`：运行小型安全 probe，并写入 probe 结果。

### 19.5 Remote Detection

`remote_detection` 通过 runner 执行安全探测命令，尝试获取：

1. GPU name
2. backend
3. device count
4. driver/runtime version
5. total memory
6. available memory
7. warp size 或 wave size
8. max threads per block
9. shared memory per block
10. register limit
11. supported dtype
12. compiler version
13. TileLang / mcTileLang version

要求：

1. 探测命令失败不能中断主流程。
2. 所有失败写入 `workspace/results/hardware_detection.log`。
3. 合并后的硬件结果写入 `workspace/results/hardware_detected.yaml`。
4. 不允许伪造探测不到的字段。
5. 只允许执行白名单内的只读命令。
6. 探测命令也必须经过 command guard。
7. 不允许执行安装、卸载、系统修改或长时间压力测试命令。
8. 不同 backend 可以有不同 detector：
   - `cuda_detector.py`
   - `mxmaca_detector.py`
   - `generic_linux_detector.py`

探测命令示例必须保持只读，例如：

```text
python -c "import platform; print(platform.platform())"
python -c "import tilelang; print(tilelang.__version__)"
```

CUDA/MACA 专用命令必须先由对应 detector 判断是否可用，不可盲目假设环境存在。

### 19.6 内置 Hardware Profile

新增内置 profile：

```text
hardware_profiles/metax_c500.yaml
hardware_profiles/unknown_gpu.yaml
```

`metax_c500.yaml` 示例：

```yaml
name: "metax-c500"
backend: "mxmaca"
source: "builtin_profile"

warp_size: null
wave_size: null
max_threads_per_block: null
shared_memory_per_block_bytes: null
max_registers_per_thread: null

total_memory_GB: 64
vector_alignment_bytes: 16

mma:
  enabled: true
  supported_dtypes: ["float16", "bfloat16"]
  preferred_m_tile: 16
  preferred_n_tile: 16
  preferred_k_tile: 32
```

Profile 规则：

1. 如果某些参数没有可靠公开来源，必须保持 `null`。
2. 不允许为了让搜索更顺畅而随便填数。
3. 每个 profile 需要有 `source: builtin_profile`。
4. report.md 需要标明哪些字段来自 builtin profile，哪些字段仍未知。
5. `unknown_gpu.yaml` 应提供最保守默认行为，不能包含虚构硬件上限。

### 19.7 Doc Lookup

`doc_lookup` V1 可以先实现为 TODO 或手动导入文档缓存。

规则：

1. 默认不联网。
2. 只有 `hardware.allow_doc_lookup=true` 或 `hardware_detection.doc_lookup=true` 时才能联网查官方文档。
3. 联网查文档必须优先使用官方来源。
4. 如果用户提供 `hardware.doc_paths`，应优先读取本地文档缓存。
5. 文档解析结果必须标记 `source=doc_lookup`。
6. 文档中没有明确写出的字段仍保持 `null`。

### 19.8 Safe Probing

如果 `shared_memory_per_block_bytes`、`max_threads_per_block`、`vector_alignment_bytes`、`NUM_STAGES` 相关约束等关键字段未知，V1 可以运行安全试探。

新增模块：

```text
hardware/safe_probe.py
```

支持 probe：

1. `threads_probe`：测试 `NUM_THREADS` 候选是否能编译/运行，只测试 `search_space` 中出现的 `NUM_THREADS`。
2. `shared_memory_probe`：用小 kernel 申请不同大小 shared memory，候选为 32KB、48KB、64KB、96KB、128KB，从小到大测试，失败后停止继续增大。
3. `vector_width_probe`：测试 `VECTOR_WIDTH` 候选是否能编译/运行，只测试 `search_space` 中出现的值。
4. `stages_probe`：测试 `NUM_STAGES` 候选是否导致明显 shared memory 编译失败。

Safe probe 要求：

1. probe 必须使用小 kernel，不允许使用完整 workload。
2. 每个 probe 必须设置 timeout。
3. probe 失败不能中断主流程。
4. probe 结果写入 `workspace/results/hardware_probe.jsonl`。
5. probe 只能推断“某配置可能可用/不可用”，不能声明完整硬件理论上限。
6. probe 不允许覆盖用户显式配置。
7. probe 结论必须标记 `source=safe_probe`。
8. probe 结论必须带 `confidence=low` 或 `confidence=medium`。
9. probe 命令必须经过 command guard。
10. probe 不得执行压力测试、系统修改、安装卸载或长时间占用 GPU 的命令。

`hardware_probe.jsonl` 建议结构：

```json
{
  "probe_name": "threads_probe",
  "candidate": {"NUM_THREADS": 256},
  "status": "probe_ok",
  "inference": "possibly_available",
  "source": "safe_probe",
  "confidence": "medium",
  "duration_seconds": 1.23,
  "stdout_path": "workspace/results/logs/probe_threads_256.stdout.log",
  "stderr_path": "workspace/results/logs/probe_threads_256.stderr.log",
  "notes": "small probe compiled and ran"
}
```

### 19.9 Conservative Unknown Mode

如果关键硬件字段仍未知，系统进入 conservative unknown mode。

触发条件示例：

1. `max_threads_per_block` 未知。
2. `shared_memory_per_block_bytes` 未知。
3. `vector_alignment_bytes` 未知。
4. backend 未知。
5. safe probe 不可用或结果不足。

保守规则：

1. 只运行 `search_space` 中较保守的配置。
2. 优先 `NUM_THREADS=128` 或 `256`。
3. 优先 `NUM_STAGES=2` 或 `3`。
4. 避免最大 `BM/BN/BK` 组合。
5. `USE_DOUBLE_BUFFER=True` 时优先小 tile。
6. 避免同时使用最大 tile、最大 stages、最大 threads 的组合。
7. 不做强结论，只输出低置信度建议。
8. report.md 必须说明硬件参数不完整，当前搜索结果可能不是最优。

Optimizer Policy 更新要求：

1. policy 输入应包含 `hardware_info` 和 `conservative_mode`。
2. grid/random/rule-based/LLM-guided/hybrid 都必须尊重硬件约束和 probe 判定。
3. 已被 probe 判定为明显不可用的参数组合不得继续推荐，除非用户显式覆盖。
4. 如果硬件信息不足，rule-based fallback 应优先保守配置。
5. 所有被 conservative mode 过滤的候选应记录原因，方便复查。

### 19.10 Report 增强

`report.md` 新增 `Hardware Detection` 部分，必须包含：

1. 用户声明的硬件名称。
2. 自动探测到的硬件信息。
3. 内置 profile 使用情况。
4. doc lookup 使用情况。
5. safe probe 结果摘要。
6. unknown 字段列表。
7. conservative mode 是否启用。
8. 每个硬件字段的 `source` 和 `confidence`。
9. 如果硬件参数不完整，需要明确说明当前搜索结果可能不是最优。

最终结果目录新增：

```text
workspace/results/hardware_detected.yaml
workspace/results/hardware_detection.log
workspace/results/hardware_probe.jsonl
```

前端结果查看器后续应读取并展示：

1. 硬件名称与 backend。
2. 字段来源分布。
3. unknown 字段数量。
4. safe probe 摘要。
5. conservative mode 状态。

### 19.11 LLM 约束

LLM 可以读取 hardware detection 结果，但必须遵守：

1. 不允许伪造未知硬件参数。
2. 不允许把 safe probe 的结果说成官方硬件上限。
3. 不允许推荐被 probe 判定为明显不可用的参数。
4. 如果硬件信息不足，必须降低诊断置信度。
5. LLM 推荐参数仍必须来自 `search_space`。
6. LLM 输出 JSON schema 应增加可选字段 `hardware_assumptions` 和 `confidence`。
7. `hardware_assumptions` 只能引用已知字段或明确写 `unknown`，不能补造数字。

LLM 输出示例：

```json
{
  "candidates": [
    {
      "config": {
        "BM": 32,
        "BN": 64,
        "BK": 32,
        "NUM_THREADS": 128,
        "NUM_STAGES": 2,
        "VECTOR_WIDTH": 2,
        "UNROLL_FACTOR": 1,
        "USE_SHARED": true,
        "USE_DOUBLE_BUFFER": false
      },
      "hypothesis": "Use a conservative tile because shared memory per block is unknown.",
      "expected_improvement": "May improve reuse while avoiding high shared-memory pressure.",
      "risk": "Hardware limits are incomplete; confidence is low.",
      "hardware_assumptions": {
        "shared_memory_per_block_bytes": "unknown",
        "max_threads_per_block": "unknown"
      },
      "confidence": "low"
    }
  ]
}
```

### 19.12 验收标准追加

后端新增验收标准：

1. `hardware_detected.yaml` 能生成。
2. 探测失败不终止主流程。
3. 探测失败写入 `hardware_detection.log`。
4. 内置 `metax_c500.yaml` 和 `unknown_gpu.yaml` 可加载。
5. 用户配置字段覆盖自动探测和 profile。
6. safe probe 失败不终止主流程。
7. `hardware_probe.jsonl` 能记录 probe 结果。
8. conservative mode 能在硬件字段不足时启用。
9. optimizer policy 能基于 conservative mode 过滤明显激进候选。
10. report.md 包含 Hardware Detection 部分。

前端新增验收标准：

1. 能展示 `hardware_detected.yaml` 的核心字段。
2. 能展示字段 source/confidence。
3. 能展示 unknown 字段列表。
4. 能展示 safe probe 摘要。
5. 能展示 conservative mode 是否启用。
6. 文件缺失时显示 waiting，不崩溃。

### 19.13 建议实现优先级

建议后续线程按以下顺序实现：

1. 新增 `hardware_info.py` 数据结构和 `hardware_profiles/unknown_gpu.yaml`。
2. 新增 `profile_loader.py`，先让 builtin profile 可加载和合并。
3. 新增 `detector.py`，生成 `hardware_detected.yaml`，即使探测全失败也能输出 unknown 字段。
4. 接入 runner 的 remote detection，只使用安全只读命令。
5. 增加 `metax_c500.yaml`，未知字段保持 `null`。
6. 将 `hardware_info` 传入 optimizer policy，先实现 conservative mode。
7. 增加 safe probe 框架和 JSONL 记录，probe kernel 可以先 mock 或最小实现。
8. 增强 report writer，加入 Hardware Detection 部分。
9. 增强前端 viewer，展示硬件信息与 unknown 字段。
10. 最后更新 LLM prompt/schema，使 LLM 读取硬件信息但不伪造未知参数。
## 20. 当前实现补充：Hardware Probe 状态语义与文档口径

本节是对第 19 章 Hardware Auto Detection and Safe Probing 的当前实现补充。后续后端、前端和文档 PR 必须保持本节语义一致。

### 20.1 `hardware_probe.jsonl` 当前结构

当前 V1 统一使用以下字段：

```json
{
  "probe_name": "threads_probe",
  "param_name": "NUM_THREADS",
  "candidate_value": 256,
  "status": "pass",
  "inference": "runner_mode=ssh_probe; NUM_THREADS=256 compiled and ran a TileLang/GPU small kernel; this is not an official hardware limit",
  "source": "safe_probe",
  "confidence": "medium",
  "stdout_path": "workspace/results/logs/hardware_probe/threads_probe_256.stdout.log",
  "stderr_path": "workspace/results/logs/hardware_probe/threads_probe_256.stderr.log"
}
```

旧版草案中的 `candidate`、`probe_ok` 等字段不再作为 V1 主 schema。前端、报告和测试应以 `probe_name / param_name / candidate_value / status / confidence / inference` 为准。

### 20.2 Probe 状态语义

`status` 允许值：

1. `pass`：probe 在当前 runner mode 下完成。
2. `failed`：probe 实际执行后失败。
3. `timeout`：probe 超时。
4. `guard_denied`：command guard 拒绝执行 probe 命令。
5. `exception`：runner 或 probe 调度阶段抛出异常。
6. `skipped`：所需后端、运行时、TileLang/mcTileLang、GPU runtime 或该 backend 的 probe 实现不可用，因此未执行真实 probe。

解释规则：

1. `pass` 只表示“当前 probe 成功”，不表示官方硬件理论上限。
2. `skipped` 不等于硬件不支持，也不等于 probe 失败；它表示没有得到真实可用性结论。
3. `failed`、`timeout`、`guard_denied`、`exception` 可以作为“明显不可用或当前不可执行”的低/中置信度信号，但不能伪装成官方硬件限制。
4. 所有 probe 结论必须保留 stdout/stderr 路径，便于复查。

### 20.3 Runner Mode 语义

当前实现区分：

1. `local_mock`
   - 用于 local runner 的 mock/demo/test 模式。
   - 不验证真实 GPU 能力。
   - 所有结论必须是 `confidence=low`。
   - 报告和前端必须明确显示“未验证真实 GPU 能力”。

2. `ssh_probe`
   - 用于 SSH runner。
   - CUDA 路径会尝试 TileLang/GPU 小 kernel 编译和运行。
   - 如果 TileLang、torch、CUDA/GPU runtime 不可用，应返回 `skipped`，而不是假装失败或通过。

3. backend-specific probe
   - mxmaca/metax 需要 backend-aware 处理。
   - 如果真实 mxmaca/metax probe 尚未实现，应返回 `skipped`，`confidence=low`，并在 reason 中写明 `backend probe not implemented`。
   - 未来实现真实 mxmaca/metax probe 后，才能在该 backend 上返回 `pass` 的真实 probe 结论。

### 20.4 Safe Probe 与 Optimizer / LLM 的关系

1. optimizer policy 和 LLM planner 可以读取 `hardware_probe.jsonl` 结果。
2. LLM 仍只能从 `search_space` 中选择参数。
3. 被 probe 判定为明显不可用的参数可以被过滤或降权。
4. `local_mock` 和 `skipped` 不应被当作真实硬件通过信号。
5. 当硬件字段仍未知，或 probe 结果不足以建立约束时，应启用或保持 conservative mode。
6. Conservative mode 不是性能优化结论，只是硬件信息不足时的保守搜索策略。

### 20.5 Report 和前端展示要求

`report.md` 和前端 viewer 必须遵守：

1. 明确说明 safe probe 是低/中置信度可用性试探，不是官方硬件上限。
2. 单独提示 `local_mock`：未验证真实 GPU 能力。
3. 单独提示 `skipped`：probe 未执行，不代表硬件不支持。
4. 显示每条 probe 的 `status`、`confidence`、`inference`、`stdout_path`、`stderr_path`。
5. 前端 V1 仍然只读结果文件，不增加 HTTP API，不触发后端命令执行。

### 20.6 当前已合并状态

截至当前 V1：

1. 后端已支持 search-space-aware safe probe。
2. 后端已支持 local mock 低置信度标记。
3. 后端已支持 SSH/CUDA TileLang GPU 小 kernel probe 尝试。
4. 后端已支持 mxmaca/metax backend-aware skip 语义。
5. optimizer policy 和 LLM prompt 已接入 hardware/probe context。
6. 前端已支持 `hardware_detected.yaml` 与 `hardware_probe.jsonl` 只读展示。
7. mxmaca/metax 真实 probe 仍是下一阶段任务，不应在文档中宣称已经完成。

## 21. V2 方向修订：算子源码优化与新架构适配

本节记录 2026-09-06 用户确认的产品方向，优先于历史“参数搜索器”定位。这是开发决策，不表示下列能力已经全部实现。

### 21.1 核心定位

产品是目标架构感知、证据驱动的算子源码优化与适配 Agent。用户通过 Web 提交原始 TileLang 算子源码和目标 GPU，使用长期保存的 SSH/LLM 设置启动任务；Agent 在真实目标环境中理解、修改、验证算子实现，返回预算内实际验证过的 best-seen kernel。

核心价值是改变算子实现以适配架构并改善性能，而不是重新实现已有 autotune 的参数组合枚举。仍不承诺全局最优或任意新架构都可自动适配。

### 21.2 优化工作内容

1. 理解算子的数学语义、输入输出、shape、dtype、布局、同步和数值精度约束。
2. 核实目标硬件、运行时和编译器能力。用户覆盖、真实探测、可靠 profile/文档、probe 的来源与置信度必须保留，未知字段不得编造。
3. 采集 benchmark、编译日志、生成代码和可用的真实 profiler 数据，形成可追溯的瓶颈假设。
4. 根据证据修改源码，例如访存合并与向量化、数据复用、shared layout、流水线与同步、寄存器/私有内存压力、冗余 HBM 写回、线程/warp 映射及可用的矩阵指令路径。
5. 对每个改动执行语法/编译检查、独立正确性检查和性能测量。正确性失败不得运行性能排名所用 benchmark；失败与退化均须记录并恢复可用版本。
6. 比较相同 workload、shape、dtype 和验证容差下的前后性能。保留运行环境、原始测量、源码、diff、假设、证据和结果，不能把测量噪声当成确定提升。

没有 profiler 时可退化为 benchmark/log/generated-code 证据，但不能据此捏造 warp 活跃度、HBM 带宽或 shared conflict 等硬件指标。

### 21.3 与参数调优的边界

1. 停止扩展自研 grid/random/LLM 参数组合搜索，不再把 search_space 编辑、候选组合或模板占位符作为普通用户入口。
2. BM/BN、线程数、stage 等参数不是绝对禁止修改，但必须服务于一个具体源码或架构优化假设，不能用仅枚举这些参数替代源码优化交付。
3. 将来确有必要寻找实现内部的配置时，可复用已有 autotune 能力；本项目重点是选择与验证实现策略，不重复建设通用参数搜索引擎。
4. 已有 V1 搜索模块、结果和 CLI 只为历史兼容暂留，不在此次方向修订中大范围删除。它们不再构成新 Web 产品的强制前提。
5. 优化预算应最终约束源码候选验证的次数/时间，不得把旧参数搜索轮数改名后当成已实现的源码优化预算。

### 21.4 用户入口与安全边界

1. 普通用户提交原始源码即可，不要求手写配置文件、`{{BM}}`、search_space 或 `BEGIN_AGENT_PATCH` 标记。
2. 后续源码优化需通过源码解析确定可授权修改的文件、函数和局部区域；不能安全确定边界时，仅验证 baseline 并说明限制，不放任 LLM 修改整个工程。
3. 正确性参考实现、检查命令、容差与测试数据不能由优化器修改来掩盖错误。涉及算子数学语义改变时，必须重新取得用户明确授权。
4. 每个源码候选有隔离 workspace、修改前快照/diff、timeout、失败记录和回滚路径。命令仍经过 command_guard，LLM 不直接执行 shell。
5. SSH、LLM 密钥、令牌和私钥不得进入日志、结果、前端存储或仓库。架构适配不包含擅自修改远程系统、驱动或软件环境。

### 21.5 当前任务与阶段门禁

1. 正在进行的同源 FastAPI、安全 sample 上传、任务隔离、普通源码 baseline-only 路径继续完成，它们是源码优化的基础运行层，不代表优化引擎已交付。
2. 只有 baseline 能力时，界面明确显示“运行基线”或“验证并测量”，结果注明“仅基线测量，未执行源码优化”。不得用旧参数搜索、mock 或 synthetic 数据冒充架构适配和源码优化成功。
3. 下一阶段打通最小的“证据 -> 一种源码改动 -> correctness + benchmark -> 接受或回滚”闭环，再扩展更多优化策略和目标架构。
4. 首个真实架构验证沿用沐曦 C500/MXMACA 目标；只有连接和运行条件确实满足才能开始真实采集。SSH/环境阻塞须独立报告，不能拿本地 mock 替代真实通过结论。
5. 最终验收包含一次真实目标 GPU 上的完整源码优化尝试、完整审计产物和可解释的接受/拒绝结果。有收益时返回验证过的改进版本；未发现收益时保留 baseline 并如实说明。正确性失败版本绝不能成为 best kernel。

前后端从 `main` 独立开分支，由协调任务验收合并。本次方向调整不要求重新启动文档任务、整体重写 README、发布安装包或冻结版本。
