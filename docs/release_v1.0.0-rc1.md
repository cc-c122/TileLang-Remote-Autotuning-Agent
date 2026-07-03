# V1.0.0-rc1: Remote Password Auth Template Autotuning Loop

## V1 能做什么

V1 提供一个可运行、可复现、可审计的 TileLang kernel 自动调参闭环。它会根据用户配置的 `search_space` 生成模板参数候选，逐个执行 correctness、build 和 benchmark，并保存当前搜索预算内的 best-seen kernel。

本版本支持：

- local/mock runner，用于本地无 GPU、无 SSH 的主流程验证。
- SSH runner，用于远程容器执行。
- SSH password authentication 主路径，SSH key auth 可作为兼容路径保留。
- `{{BM}}`、`{{BN}}`、`{{NUM_THREADS}}` 等模板占位符替换。
- `grid`、`random`、`rule_based`、`llm`、`hybrid` 搜索策略。
- correctness gate：正确性失败的候选不会进入性能排名。
- benchmark stdout 指标解析，包括 latency、TFLOPS 和 bandwidth。
- JSONL、CSV、stdout/stderr logs、patch、best kernel、best config 和 Markdown report 产物。
- 只读前端结果查看器。
- 硬件探测和 safe probe 结果展示。

## 用户需要提供什么

用户需要准备：

- TileLang kernel sample 文件或目录。
- `kernel.entry_file`，即包含模板占位符的入口文件。
- `correctness_command`。
- 可选 `build_command`。
- `run_command` / benchmark 命令。
- 必填 `search_space`，且 key 必须和模板占位符完全一致。
- benchmark 指标解析规则，或使用推荐的强约定输出格式。
- 远程运行所需的 SSH host、port、username 和 `remote_workspace`。
- password auth 场景下的 `remote.password_env` 环境变量名，以及本机 shell 中对应的密码环境变量值。

## 安装方式

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

V1.0.0-rc1 暂不发布 PyPI。安装包先作为 GitHub Release assets 提供。

## Password Auth

V1 SSH runner 必须支持 password authentication。配置文件只保存环境变量名，不保存密码值：

```yaml
remote:
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
```

Bash：

```bash
export KERNEL_AGENT_SSH_PASSWORD='<container-password>'
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = '<container-password>'
```

真实密码不得写入 `config.yaml`、日志、JSONL、CSV、报告、异常堆栈、前端展示或 PR / issue 评论。

## 不保证全局最优

V1 不承诺全局最优。它只返回当前配置、搜索空间、硬件环境和搜索预算内实际运行得到的 best-seen kernel。报告中的性能结论应理解为当前实验预算内的观测结果，而不是完整硬件/算法空间的最优结论。

## 当前限制

- 只支持模板参数替换，不支持 LLM 自由重写完整 kernel。
- LLM 只能从 `search_space` 中选择参数，不能生成或执行 shell command。
- 前端是只读结果查看器，不提供后端 HTTP API。
- 不安装系统包，不修改远程系统环境。
- profiler 仍是扩展接口，复杂 profiler 指标采集不属于本次 rc1 范围。
- safe probe 是低/中置信度可用性试探，不是官方硬件理论上限。
- 远程 workspace 必须是项目可管理目录；非空且未标记 `.kernel_opt_agent_workspace` 的目录会被拒绝清理。
- V1.0.0-rc1 仍是 release candidate，建议先用 local/mock 和 SSH password smoke 路径验证后再用于更大的调参任务。
