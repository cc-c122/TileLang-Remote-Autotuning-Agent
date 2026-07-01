# TileLang Remote Autotuning Agent 项目说明书

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
7. SSH Runner V1 必须完整支持 SSH key authentication；password authentication 保留配置字段，但实现可以后置。
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
  auth_type: key
  key_path: ~/.ssh/id_rsa
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
5. `remote.auth_type=key` 时必须提供 `remote.key_path`，V1 必须完整支持 key auth。
6. `remote.auth_type=password` 时必须提供 `remote.password_env`，不得在配置文件中直接写密码；如果当前版本尚未实现 password auth，程序必须给出明确错误提示。
7. `llm.provider` V1 只能是 `openai_compatible`。
8. `llm.api_key_env` 只保存环境变量名，不直接保存 API Key。
9. `kernel.sample_path` 必须存在，可以是单个文件或 sample 目录。
10. 当 `sample_path` 是目录时，`kernel.entry_file` 必须是该目录下的相对路径；V1 只对 `entry_file` 做模板渲染，其他文件原样上传。
11. `timeout_seconds`、`max_iterations`、`candidates_per_iteration` 必须为正整数。
12. `search.strategy` 只能是 `grid`、`random`、`llm`、`rule_based`、`hybrid`，默认建议为 `hybrid`。
13. `search_space` 是 V1 必填字段，不能为空。
14. 每个模板占位符如果需要被调参，必须在 `search_space` 中声明。
15. `search_space` 参数名必须和模板占位符完全一致，并且区分大小写。
16. 如果模板里出现未替换占位符，程序必须报错。
17. boolean 参数渲染到 Python 文件时统一使用 Python 字面量 `True` / `False`。
18. `constraints.denied_commands` 必须合并默认高危命令列表，用户不能通过配置清空安全红线。

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
8. V1 必须完整支持 SSH key authentication。
9. password authentication 保留配置字段；若当前版本未实现而用户配置 `auth_type=password`，必须给出明确错误提示。
10. password、token、API key 不得写入日志。

认证配置示例：

```yaml
runner:
  type: ssh

remote:
  host: example.com
  port: 22
  username: root
  auth_type: key
  key_path: ~/.ssh/id_rsa
  remote_workspace: /root/kernel_opt_workspace
```

password auth 预留配置：

```yaml
remote:
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
```

要求：

1. 不允许把 password 直接写入 `config.yaml`，必须通过环境变量读取。
2. `password_env` 只保存环境变量名。
3. 如果 `auth_type=password` 但当前版本未实现，程序应 fail fast 并给出明确错误提示，而不是静默失败。

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
4. V1 优先且必须完整支持 SSH key authentication。
5. password authentication 保留配置字段，允许后置实现；如果用户配置但当前版本未实现，程序必须明确报错。
6. 密码必须通过 `password_env` 指定的环境变量读取，不允许写入 `config.yaml`。
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
