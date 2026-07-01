# TileLang Remote Autotuning Agent

TileLang Remote Autotuning Agent 是一个用于优化 TileLang kernel sample 的远程自动调参智能体。V1 目标是提供一个可运行、可复现、可扩展的自动调参闭环：上传用户提供的 kernel sample 到远程 workspace，执行 correctness/build/benchmark，解析结果，生成下一批模板参数候选，持续搜索，并输出当前预算内的 best-seen kernel。

重要边界：本项目不承诺全局最优，只返回当前搜索预算内发现的最优版本。

## 当前状态

V1 local/mock MVP 已通过验收，包含：

- local runner 完整闭环
- SSH runner 基础实现与 key-auth 配置示例
- command guard 安全检查
- correctness 与 benchmark 强格式解析
- grid/random/rule-based/LLM-guided/hybrid 搜索策略
- OpenAI-compatible Chat Completions LLM client
- 结果持久化与 Markdown 报告
- 只读前端结果查看器
- V1 hardening 单元测试

仍待实机验证：

- SSH key-auth 远程容器 smoke test
- 真实远程结果在前端查看器中的展示验收

## 目录结构

```text
.
  instruction.md                  # 项目说明书与架构决策
  main.py                         # 根入口，转发到 kernel_opt_agent.main
  tests/                          # V1 hardening 测试
  kernel_opt_agent/
    README.md                     # 包内运行说明
    config.example.yaml           # local/mock 示例配置
    config.ssh.example.yaml       # SSH smoke 示例配置
    requirements.txt
    main.py
    agent/
    benchmark/
    frontend/
    kernel/
    profiler/
    runner/
    samples/mock/
    storage/
    workspace/results/
```

## 快速开始

安装依赖：

```bash
pip install -r kernel_opt_agent/requirements.txt
```

运行 local/mock 示例：

```bash
python main.py --config kernel_opt_agent/config.example.yaml
```

运行测试：

```bash
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
```

结果会生成在：

```text
kernel_opt_agent/workspace/results/
```

关键产物：

- `experiments.jsonl`
- `summary.csv`
- `failed_cases.jsonl`
- `all_results.csv`
- `best_kernel.py`
- `best_config.yaml`
- `report.md`

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

V1 要求：

- 完整支持 SSH key authentication。
- `password` auth 保留配置字段，但暂未实现。
- 如果配置 `auth_type: password`，程序会明确报错。
- 不允许把 SSH 密码、API key、token 写入配置或日志。

## 配置要点

模板占位符统一使用 `{{NAME}}`：

```python
BM = {{BM}}
BN = {{BN}}
num_threads = {{NUM_THREADS}}
use_shared = {{USE_SHARED}}
```

`search_space` 必填，且参数名必须和模板占位符完全一致、区分大小写：

```yaml
search_space:
  BM: [16, 32, 64]
  BN: [32, 64]
  NUM_THREADS: [128, 256]
  USE_SHARED: [true, false]
```

boolean 参数渲染到 Python 文件时会输出 `True` / `False`。

## 输出格式约定

benchmark stdout 优先使用强约定格式：

```text
BENCHMARK_RESULT latency_ms=<float> tflops=<float> bandwidth_gbps=<float>
```

correctness stdout 优先使用强约定格式：

```text
CORRECTNESS_RESULT status=<PASS|FAIL> max_error=<float> reason="<text>"
```

正确性失败的候选会记录到结果文件，但不会进入性能排名。

## 前端结果查看器

前端位于：

```text
kernel_opt_agent/frontend/index.html
```

V1 前端是只读结果查看器：

- 不调用后端 HTTP API
- 不执行 shell 命令
- 不读取密钥
- 不修改结果文件

使用方式：

1. 在 Chromium 浏览器中打开 `kernel_opt_agent/frontend/index.html`，点击选择 `kernel_opt_agent/workspace/results/`。
2. 或用静态服务器服务 `kernel_opt_agent/`，打开 `/frontend/index.html`，页面会读取 `../workspace/results/`。

## 安全限制

- 所有远程命令必须经过 `command_guard.py`。
- 命令默认在 workspace 根目录执行。
- V1 不支持每条命令单独配置 working directory。
- 禁止危险命令片段，例如 `rm -rf`、`mkfs`、`dd if=`、`apt remove`、`shutdown`、`reboot`。
- LLM 不能直接执行 shell command。
- LLM 只能从 `search_space` 中选择参数。
- API key、SSH password、token 不得写入日志、结果文件或报告。

## 协作流程

`main` 分支必须保持可运行。所有开发从 `main` 拉分支，通过 PR 合并。

建议分支名：

```bash
backend/ssh-real-smoke
frontend/ssh-results-view
```

PR 验收前至少运行：

```bash
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
python main.py --config kernel_opt_agent/config.example.yaml
```

不要提交：

- 真实 `config.yaml`
- SSH key、API key、token、password
- 本地实验日志
- `workspace/generated/`
- `workspace/patches/`
- `workspace/results/` 中的运行产物

## 版本计划

下一阶段目标：

1. 完成 SSH key-auth 真实远程容器 smoke test。
2. 用真实 SSH results 验证前端查看器。
3. 整理 V1 release checklist。
4. 通过后打 `v0.1.0` tag。
