# V1 Release Checklist

本文用于 V1 发布前最终检查。范围只覆盖当前 V1 能力，不扩展后续版本内容。

## 发布前必过检查

### 代码与测试

- [ ] `python -m unittest discover -s tests`
- [ ] `python -m compileall -q kernel_opt_agent tests`
- [ ] `python main.py --config kernel_opt_agent/config.example.yaml`
- [ ] `kernel_opt_agent/config.ssh.example.yaml` 能被 `load_config()` 正常读取。
- [ ] local/mock 结果能生成 `report.md`、`summary.csv`、`experiments.jsonl`、`best_kernel.py` 和 `best_config.yaml`。

### SSH Password Auth

- [ ] `remote.auth_type: password` 是 SSH 示例配置默认路径。
- [ ] `remote.password_env` 只保存环境变量名，不保存密码值。
- [ ] 缺失密码环境变量时给出明确错误。
- [ ] password auth 真实 smoke 已通过，且有脱敏报告记录。
- [ ] SSH key auth 只作为兼容路径保留。

### 安全与脱敏

- [ ] `config.yaml`、`effective_config.yaml`、日志、JSONL、CSV、报告中不包含真实 password、token 或 API key。
- [ ] `remote.key_path` 在脱敏配置中不泄漏本地私钥路径。
- [ ] command guard 能拒绝高风险命令片段。
- [ ] 远程 workspace 非空且没有 `.kernel_opt_agent_workspace` marker 时拒绝清理。
- [ ] 文档和 PR 描述不包含真实 host、真实端口、真实用户名、真实密码或私有 token。

### 结果文件与前端

- [ ] 前端查看器只读结果文件，不调用后端 HTTP API。
- [ ] 前端能展示 report、summary、trial 状态、失败原因和日志路径。
- [ ] 缺失结果文件时前端显示 waiting 或空状态，不崩溃。
- [ ] `hardware_detected.yaml` 和 `hardware_probe.jsonl` 缺失或为空时，前端和报告能降级显示。

### 文档

- [ ] 根 `README.md` 说明项目用途、快速开始、配置、输出、安全限制和当前限制。
- [ ] `kernel_opt_agent/README.md` 不再写 password auth 未实现。
- [ ] `instruction.md` 明确 V1 必须支持 password auth。
- [ ] `docs/v1_password_auth_smoke.md` 只使用占位符示例。
- [ ] `docs/v1_password_auth_smoke_report.md` 是脱敏报告。

## V1 Release Note 草稿

### V1 能做什么

V1 提供一个可运行、可复现、可审计的 TileLang kernel 自动调参闭环。用户给出带模板占位符的 kernel sample、correctness 命令、benchmark 命令和 `search_space` 后，Agent 会生成候选 kernel，按 trial 执行 correctness、build 和 benchmark，记录成功/失败、日志、patch、指标和 best-seen 结果。

V1 支持：

- local/mock runner，用于无 GPU、无 SSH 的本地验证。
- SSH runner，用于远程容器执行。
- template-based 参数替换，不让 LLM 自由改写完整 kernel。
- `grid`、`random`、`rule_based`、`llm`、`hybrid` 搜索策略。
- correctness gate，正确性失败的候选不会进入性能排名。
- benchmark 指标解析，包括 latency、TFLOPS 和 bandwidth。
- JSONL、CSV、best kernel、best config 和 Markdown report 产物。
- 只读前端结果查看器。
- 硬件探测和 safe probe 的结构化记录与展示。

### 用户需要提供什么

用户需要准备：

- TileLang kernel sample 文件或目录。
- `kernel.entry_file`，也就是需要渲染模板占位符的入口文件。
- `correctness_command`。
- 可选 `build_command`。
- `run_command` / benchmark 命令。
- 必填 `search_space`，且 key 必须和模板占位符完全一致。
- 指标解析规则，或使用推荐的强约定 benchmark 输出格式。
- 远程运行时的 SSH host、port、username、`remote_workspace`。
- 远程 password auth 时，通过 `remote.password_env` 指定环境变量名，并在本机 shell 中设置密码值。

### 不保证全局最优

V1 不承诺全局最优。它只返回当前配置、搜索空间、硬件环境和搜索预算内实际运行得到的 best-seen kernel。报告中的性能结论应理解为当前实验预算内的观测结果，而不是完整硬件/算法空间的最优结论。

### Password Auth 支持

V1 SSH runner 必须支持 password authentication。配置文件只允许保存环境变量名：

```yaml
remote:
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
```

真实密码只能通过本机 shell 环境变量传入，不能写入配置、日志、JSONL、CSV、报告、异常堆栈或 PR 评论。SSH key auth 可作为兼容路径保留，但不是 V1 远程 smoke 的默认路径。

### 当前限制

- V1 不做全局最优搜索保证。
- V1 只做模板参数替换，不支持 LLM 自由重写完整 kernel。
- V1 不提供后端 HTTP API；前端只读结果文件。
- V1 不安装系统包，不修改远程系统环境。
- V1 的 profiler 仍是扩展接口，复杂 profiler 指标采集不属于当前发布范围。
- safe probe 结果是低/中置信度可用性试探，不是官方硬件理论上限。
- 远程 workspace 必须是项目可管理目录；非空且未标记的目录会被拒绝清理。
