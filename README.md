# TileLang Remote Autotuning Agent

TileLang Remote Autotuning Agent 是一个用于优化 TileLang kernel sample 的远程自动调参智能体。

它要解决的问题很直接：用户已经有一个可以运行的 TileLang 算子样例，但不知道哪些 tile size、线程数、stage 数、向量宽度、unroll 参数更快。这个项目会在安全约束下把候选 kernel 上传到远程算力容器，自动跑 correctness、build 和 benchmark，记录成功与失败，继续生成下一批候选，最后给出当前搜索预算内找到的 best-seen kernel。

重要边界：V1 不承诺全局最优。它只返回在当前配置、搜索空间和搜索预算内实际试出来的最优版本。

## 一句话概括

你提供：

- 一个 TileLang kernel sample
- correctness 命令
- build 命令
- benchmark 命令
- 参数搜索空间
- 远程 SSH 容器信息
- 可选的 LLM API 配置

Agent 自动做：

1. 复制 sample 到工作区。
2. 根据 `{{BM}}`、`{{BN}}`、`{{NUM_THREADS}}` 等模板占位符生成候选 kernel。
3. 每个候选先跑 correctness。
4. correctness 通过后再 build 和 benchmark。
5. 从 stdout 解析 latency、TFLOPS、bandwidth。
6. 把成功、失败、日志、patch、配置全部记录下来。
7. 用 grid/random/rule-based/LLM-guided/hybrid 策略生成下一批候选。
8. 在预算结束后输出 best-seen kernel、best config 和报告。

## 当前状态

V1 local/mock MVP 已通过验收，并已合并到 `main`。

已具备：

- local runner：不需要 GPU 或 SSH，也能跑通完整流程。
- SSH runner：支持 SSH key authentication 的远程执行路径和配置示例。
- command guard：远程命令执行前做安全检查。
- template renderer：只做模板参数替换，不让 LLM 自由改整个文件。
- correctness gate：正确性失败的候选不会进入性能排名。
- benchmark parser：解析 latency、TFLOPS、bandwidth。
- optimizer policy：支持 `grid`、`random`、`rule_based`、`llm`、`hybrid`。
- LLM planner：支持 OpenAI-compatible Chat Completions API，输出必须是严格 JSON。
- experiment storage：保存 JSONL、CSV、日志、patch 和报告。
- frontend viewer：只读结果文件，不执行命令。
- unit tests：覆盖 V1 hardening 关键路径。

仍待实机确认：

- SSH key-auth 远程容器 smoke test。
- 前端查看真实 SSH 结果产物。

## 它是怎么工作的

整体流程如下：

```text
config.yaml
   |
   v
load and validate config
   |
   v
copy sample into workspace
   |
   v
generate candidate config from search_space
   |
   v
render entry_file template
   |
   v
save patch / trial config
   |
   v
run correctness
   |
   +-- failed --> record failed case and continue
   |
   v
run build
   |
   +-- failed --> record failed case and continue
   |
   v
run benchmark
   |
   +-- failed or parse error --> record failed case and continue
   |
   v
parse metrics and update best-seen
   |
   v
generate next candidates
   |
   v
write final report and artifacts
```

所有候选都以 trial 为单位记录。单个候选失败不会中断整个调参流程。

## 输入是什么

核心输入来自配置文件。

最小配置包括：

- `runner.type`：`local` 或 `ssh`
- `kernel.sample_path`：sample 文件或目录
- `kernel.entry_file`：需要渲染模板的入口文件
- `kernel.correctness_command`：正确性检查命令
- `kernel.build_command`：构建命令，可选
- `kernel.run_command`：benchmark 命令
- `search.strategy`：搜索策略
- `search_space`：参数候选值
- `metrics.*_regex`：兼容解析 benchmark 输出的 regex
- `constraints.denied_commands`：额外禁止命令片段

示例：

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

完整示例见：

- [kernel_opt_agent/config.example.yaml](kernel_opt_agent/config.example.yaml)
- [kernel_opt_agent/config.ssh.example.yaml](kernel_opt_agent/config.ssh.example.yaml)

## 模板是怎么生成候选的

V1 不允许 LLM 自由改写整个 kernel 文件。候选生成只做模板参数替换。

入口文件里写占位符：

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

配置文件里必须声明同名 `search_space`：

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

规则：

- 占位符名必须和 `search_space` key 完全一致。
- 名字区分大小写。
- `search_space` 不能为空。
- LLM、random search、rule-based fallback 都不能生成 `search_space` 外的值。
- boolean 会渲染为 Python 字面量 `True` / `False`。
- 如果模板里还有未替换占位符，程序会报错。

## correctness 和 benchmark 输出格式

V1 推荐使用强约定输出格式，解析最稳定。

correctness stdout：

```text
CORRECTNESS_RESULT status=<PASS|FAIL> max_error=<float> reason="<text>"
```

示例：

```text
CORRECTNESS_RESULT status=PASS max_error=0.00001 reason="ok"
```

benchmark stdout：

```text
BENCHMARK_RESULT latency_ms=<float> tflops=<float> bandwidth_gbps=<float>
```

示例：

```text
BENCHMARK_RESULT latency_ms=1.23 tflops=120.5 bandwidth_gbps=850.0
```

如果 benchmark 没有强格式，程序会尝试用配置里的 regex 做兼容解析。解析失败会记录为 `parse_error`，不会中断全局流程。

## 搜索策略

`search.strategy` 支持：

- `grid`：按 `search_space` 确定性枚举。
- `random`：固定 seed，从 `search_space` 中采样。
- `rule_based`：使用保守启发式，从小/中/大配置里选候选。
- `llm`：调用 LLM 生成候选，但必须通过 JSON schema 和 `search_space` 校验。
- `hybrid`：默认推荐。先尝试 LLM，失败时 fallback 到 rule-based，再用 grid/random 补足候选数。

LLM 只负责提出下一批参数组合和优化假设，不能执行命令，也不能生成 shell command。

## 输出是什么

运行结束后会写入：

```text
kernel_opt_agent/workspace/results/
```

关键文件：

- `experiments.jsonl`：每个 trial 的完整结构化记录。
- `summary.csv`：便于快速浏览的摘要表。
- `failed_cases.jsonl`：失败候选记录。
- `all_results.csv`：最终结果 CSV。
- `best_kernel.py`：当前预算内 best-seen kernel。
- `best_config.yaml`：best-seen 参数配置。
- `report.md`：最终 Markdown 报告。
- `logs/`：每个 trial 的 stdout/stderr。
- `hardware_detected.yaml`：硬件探测合并结果，包含字段来源和置信度。
- `hardware_probe.jsonl`：硬件 safe probe 记录，包含 probe 状态、参数值、置信度和日志路径。

这些运行产物默认不会提交到 Git。

## 硬件探测与 safe probe 状态说明

V1 支持硬件自动探测和 safe probe，但 probe 结果必须按置信度解释，不能当作官方硬件上限。

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

`status` 语义：

- `pass`：当前 runner mode 下 probe 成功。
- `failed`：probe 实际执行后失败。
- `timeout`：probe 超时。
- `guard_denied`：command guard 拒绝执行。
- `exception`：runner 或 probe 调度异常。
- `skipped`：后端、运行时或 probe 实现不可用，因此没有执行真实 probe。

特别注意：

- `local_mock` 只用于 local/demo/test，不验证真实 GPU 能力，必须按低置信度看待。
- `skipped` 不等于硬件不支持，也不等于失败，只表示没有得到真实 probe 结论。
- SSH/CUDA 路径会尝试 TileLang/GPU 小 kernel；mxmaca/metax 如果真实 probe 尚未实现，应返回 `skipped` 和低置信度。
- 前端只读展示这些结果，不会执行命令，也不会把 `local_mock` 或 `skipped` 展示成真实硬件能力通过。

## 快速开始：local/mock 模式

安装依赖：

```bash
pip install -r kernel_opt_agent/requirements.txt
```

运行 mock 示例：

```bash
python main.py --config kernel_opt_agent/config.example.yaml
```

运行测试：

```bash
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
```

mock sample 不需要 GPU，也不需要 SSH。它用于确认主流程、失败记录、报告生成和前端展示是否可用。

## SSH 远程运行

复制 SSH 示例配置，但不要提交真实配置：

```bash
cp kernel_opt_agent/config.ssh.example.yaml ssh.config.yaml
```

填写：

- `remote.host`
- `remote.port`
- `remote.username`
- `remote.key_path`
- `remote.remote_workspace`

运行：

```bash
python main.py --config ssh.config.yaml
```

SSH Runner 的语义：

- 使用 SSH key authentication。
- 上传 sample 文件或目录到远程 workspace。
- 只渲染 `kernel.entry_file`。
- correctness/build/benchmark 默认都在 `remote.remote_workspace` 下执行。
- 拉回远程结果和日志。

V1 暂不实现 SSH password authentication。如果配置 `auth_type: password`，程序会给出明确错误。

## 前端结果查看器

前端文件：

```text
kernel_opt_agent/frontend/index.html
```

它是一个只读的本地结果查看器。

它会读取：

- `report.md`
- `summary.csv`
- `experiments.jsonl`
- `failed_cases.jsonl`
- `all_results.csv`

它展示：

- baseline 性能
- best-seen 性能
- 提升比例
- best config
- 每个 trial 的状态
- failed cases
- stdout/stderr 路径
- patch 路径
- report 内容

使用方式：

1. 在 Chromium 浏览器中打开 `kernel_opt_agent/frontend/index.html`，点击选择 `kernel_opt_agent/workspace/results/`。
2. 或用静态服务器服务 `kernel_opt_agent/`，打开 `/frontend/index.html`，页面会读取 `../workspace/results/`。

前端 V1 不做这些事：

- 不调用后端 HTTP API。
- 不执行 shell 命令。
- 不读取 SSH key 或 API key。
- 不修改结果文件。

## 安全设计

V1 把安全放在主流程里，而不是作为额外补丁。

命令安全：

- 所有远程命令都必须经过 `command_guard.py`。
- 命令默认在 workspace 根目录执行。
- V1 不支持每条命令单独配置 working directory。
- 禁止危险命令片段，例如 `rm -rf`、`mkfs`、`dd if=`、`apt remove`、`shutdown`、`reboot`。
- 禁止明显跳出 workspace 的危险操作。

敏感信息安全：

- API key 只能通过环境变量读取。
- SSH password 暂不实现，配置字段只保留。
- 不允许把 API key、SSH password、token 写进配置、日志、JSONL、CSV 或报告。
- 前端会对疑似 token/password/API key 的文本做基础脱敏。

LLM 安全：

- LLM 不能直接执行 shell command。
- LLM 输出必须是严格 JSON。
- LLM 输出必须通过 schema 校验。
- LLM 只能选择 `search_space` 中已有的值。
- LLM 调用失败时自动 fallback，不中断全局调参流程。

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
      optimizer_policy.py
      planner.py
      llm_client.py
      diagnosis.py
      prompt_templates.py
    benchmark/
      correctness.py
      parser.py
      metrics.py
    kernel/
      template_manager.py
      variant_generator.py
      patch_manager.py
    runner/
      command_guard.py
      local_runner.py
      ssh_runner.py
    storage/
      experiment_db.py
      report_writer.py
    profiler/
      base.py
      dummy_profiler.py
      mxmaca_profiler.py
    frontend/
      index.html
    samples/mock/
    workspace/results/
```

主要模块职责：

- `config_model.py`：读取和校验 YAML 配置。
- `main.py`：调度完整搜索流程。
- `runner/local_runner.py`：本地 mock/demo/test 执行器。
- `runner/ssh_runner.py`：SSH/SFTP 远程执行器。
- `runner/command_guard.py`：命令安全检查。
- `kernel/template_manager.py`：模板占位符解析与替换。
- `kernel/variant_generator.py`：候选 kernel 生成。
- `kernel/patch_manager.py`：保存候选 patch。
- `benchmark/correctness.py`：解析 correctness 输出。
- `benchmark/parser.py`：解析 benchmark 指标。
- `agent/optimizer_policy.py`：搜索策略。
- `agent/planner.py`：LLM 候选规划。
- `storage/experiment_db.py`：写 JSONL、CSV、日志。
- `storage/report_writer.py`：生成最终报告和 best kernel。
- `frontend/index.html`：只读结果查看器。

## 协作流程

`main` 分支必须保持可运行。所有开发从 `main` 拉分支，通过 PR 合并。

常用分支名：

```bash
backend/ssh-real-smoke
frontend/ssh-results-view
```

开发前：

```bash
git checkout main
git pull
git checkout -b your-branch-name
```

PR 验收前至少运行：

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
- `workspace/generated/`
- `workspace/patches/`
- `workspace/results/` 中的运行产物

## 当前限制

- V1 不保证全局最优。
- V1 只做模板参数替换，不让 LLM 自由改完整 kernel。
- V1 暂不实现 SSH password authentication。
- V1 暂不实现复杂 profiler，只保留 profiler 接口。
- V1 前端不提供 HTTP API。
- SSH 真实容器 smoke test 仍需执行并验收。

## 下一阶段计划

1. 完成 SSH key-auth 真实远程容器 smoke test。
2. 用真实 SSH results 验证前端查看器。
3. 整理 V1 release checklist。
4. 补充已知限制和 troubleshooting。
5. 通过后打 `v0.1.0` tag。
