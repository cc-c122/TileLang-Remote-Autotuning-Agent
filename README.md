# TileLang Remote Autotuning Agent

TileLang Remote Autotuning Agent 是一个面向 TileLang kernel sample 的远程调参工具：用户在 Web 页面输入或上传 sample、选择 GPU、使用长期保存的 SSH/LLM 设置，点击“开始优化”，系统会在受保护的 workspace 内远程执行候选 kernel，并返回当前预算内实际测到的 best-seen kernel。

配置文件仍然存在，但在当前 V2 开发态里，它们主要是内部实现和高级调试入口；普通用户主流程是 FastAPI Web API + 单文件 Web UI。

## 当前主流程

1. 在 Web 设置页保存 SSH 和 LLM 的非密钥配置，并测试连接。
2. 在“新建任务”里提供 sample、build/correctness/benchmark 命令、GPU 型号和搜索预算。
3. 后端创建任务，写入任务 workspace，并按顺序执行 build、correctness、benchmark、profiler、diagnosis 和受控 patch 流程。
4. 在“任务详情”和“实验结果”里查看事件、best kernel、report、失败样例和证据。

系统不保证全局最优，只返回当前预算内已经运行并通过 correctness 的 best-seen kernel。

## 快速开始

需要 Python 3.10+。

```bash
pip install -r kernel_opt_agent/requirements.txt
python -m kernel_opt_agent.server.app
```

默认服务地址是：

```text
http://127.0.0.1:8765
```

后端 API 默认监听 `127.0.0.1:8765`。Web UI 是 `kernel_opt_agent/frontend/index.html`，页面会调用同源 `/api/*` 接口；当前后端不负责挂载这个静态文件。开发时请按当前调试环境的静态服务或代理方式打开 `frontend/index.html`，并确保页面所在 origin 能访问同源 `/api/*`。

启动后建议先进入“设置”页：

- 填写 SSH host、port、username、auth type、remote workspace 等非密钥字段。
- 填写 LLM provider、base URL、model、API key 环境变量名。
- 点击连接测试，确认远程 SSH 可达。
- 再回到“新建任务”创建优化任务。

## 密钥与环境变量

SSH 密码和 LLM API key 不保存明文。设置文件和服务端保存的配置只记录环境变量名，例如 `KERNEL_AGENT_SSH_PASSWORD` 和 `OPENAI_API_KEY`。`SettingsStore` 会拒绝保存明文 secret 字段，并在返回设置时做脱敏和环境变量存在性提示。

PowerShell:

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = "your-ssh-password"
$env:OPENAI_API_KEY = "your-llm-api-key"
python -m kernel_opt_agent.server.app
```

bash:

```bash
export KERNEL_AGENT_SSH_PASSWORD="your-ssh-password"
export OPENAI_API_KEY="your-llm-api-key"
python -m kernel_opt_agent.server.app
```

不要把真实密码、API key、token 或私钥内容写进 README、settings、run request、日志、PR 描述或结果文件。

## Web 页面

当前 Web 控制台包含这些页面：

- 新建任务：填写 sample、命令、目标 GPU、runner、预算、profiler 和 patch 开关。
- 任务详情：查看任务状态、事件流、取消任务。
- 实验结果：查看 best kernel、best config、report、summary、失败 case，并下载 `best_kernel.py` 和 `report.md`。
- 硬件画像：根据 GPU 型号解析内置 profile，展示字段来源和置信度，允许用户覆盖硬件参数。
- 设置：长期保存 SSH/LLM 非密钥配置，测试 SSH 连接。
- 开发者模式：导出 `run_request.yaml` 和 `settings.yaml`，用于 CLI fallback 和调试；这不是普通用户入口。

## 真实功能

当前代码已经具备：

- FastAPI Web API：settings 保存/读取/连接测试、hardware resolve、task 创建/列表/详情/events/results/download/cancel。
- SSH runner：支持 password auth 和 key auth；password 只从环境变量读取。
- 远程 workspace 管理：使用 `.kernel_opt_agent_workspace` 标记受管目录，拒绝危险路径和未标记的非空目录。
- correctness-before-benchmark：候选必须先通过 correctness，失败候选不会进入性能排名。
- 参数搜索：Web 任务当前使用 `rule_based` 策略和 `latency` 目标；候选失败会记录，不会中断整个任务。
- 硬件 resolve、探测、profile 和 safe probe：字段带来源与置信度；自动补全不是准确性保证。
- profiler 和日志解析：支持 dummy、TileLang log、mcProfiler 结果解析路径；拿不到的指标保持 `null`。
- evidence 和 diagnosis：根据 profiler、benchmark、log 证据给出瓶颈判断；证据不足时输出 `insufficient_evidence`。
- 受控 patch：只允许修改显式标记区域，执行语法/build/correctness/benchmark 校验，并在结束后 rollback。
- 报告与产物：生成 report、best kernel、best config、CSV/JSONL 记录和失败 case。

## API 概览

后端应用标题是 `TileLang Remote Autotuning Agent API`，当前路由包括：

```text
GET  /api/health
POST /api/settings
GET  /api/settings
POST /api/settings/test-connection
POST /api/hardware/resolve
POST /api/tasks
GET  /api/tasks
GET  /api/tasks/{task_id}
GET  /api/tasks/{task_id}/events
GET  /api/tasks/{task_id}/results
GET  /api/tasks/{task_id}/download/best_kernel
GET  /api/tasks/{task_id}/download/report
POST /api/tasks/{task_id}/cancel
```

任务会写入：

```text
kernel_opt_agent/workspace/tasks/{task_id}/
kernel_opt_agent/workspace/tasks/{task_id}/results/
```

其中会保存 `run_request.yaml`、`effective_config.yaml`、sample、generated kernel、patches 和 results。

## 结果与证据

常见结果文件包括：

- `best_kernel.py`：当前预算内 best-seen kernel。
- `best_config.yaml`：best-seen 参数配置。
- `report.md`：本次任务报告。
- `summary.csv` / `all_results.csv`：候选汇总。
- `experiments.jsonl`：每个 trial 的结构化记录。
- `failed_cases.jsonl`：correctness、build、benchmark 或 guard 失败记录。
- `profiler_results.jsonl`：profiler 采集或解析结果。
- `metric_observations.jsonl`：指标证据快照。
- `diagnosis.jsonl` / `diagnoses.jsonl`：瓶颈诊断。
- `patch_trials.jsonl`：受控 patch 尝试记录。
- `logs/`：trial stdout/stderr。

指标缺失时字段应保持 `null`，表示没有可靠证据；系统不会伪造 profiler 指标或性能数据。诊断置信度遵循证据强度：profiler 多证据一致通常为 high，benchmark + log 通常为 medium，单一线索或缺字段通常为 low；没有 profiler 时退化为 benchmark/log based diagnosis。

## 搜索与优化边界

V2 当前把模板参数搜索和 evidence-guided controlled patch 组合起来使用：

- 模板参数来自 sample 中的占位符和任务预算；Web 任务默认走 `rule_based`。
- 所有候选都必须先过 correctness，correctness 失败不参与排名。
- benchmark 失败、命令超时、guard 拦截、profiler 缺失都会被记录为证据或失败 case，通常不会让整个任务直接停止。
- 受控 patch 只允许修改 `# BEGIN_AGENT_PATCH: name` 和 `# END_AGENT_PATCH` 包围的区域。
- patch 校验会拒绝 `subprocess.`、`os.system`、`shell=True`、嵌套 patch 标记等危险片段。
- patch 运行后会 rollback，避免把候选修改长期留在 workspace 中。

LLM 只参与建议和生成受控候选，不拥有执行 shell 的权限，也不应被描述为会联网搜索硬件事实。硬件参数自动补全必须保留来源和置信度；`unknown` 和 `null` 是合法状态。

## 安全限制

远程命令受 `kernel_opt_agent/runner/command_guard.py` 约束。当前 guard 会拦截高风险片段，例如 `rm -rf`、`mkfs`、`dd if=`、shutdown/reboot、系统包移除、`curl | sh`、`wget | sh`、`chmod 777 /`、跳出 workspace 的 `cd` 和明显针对系统路径的破坏性重定向。

SSH runner 会：

- 在远程 workspace 内执行命令。
- 要求 workspace 是安全路径。
- 使用 `.kernel_opt_agent_workspace` 标记受管目录。
- 拒绝清理未标记的非空 workspace。
- 通过 SFTP 上传 sample 和拉回结果。

settings 只保存非密钥字段和环境变量名；错误信息会尽量脱敏。真实 secret 必须由运行后端的 shell 环境提供。

## 当前限制

- 不保证全局最优，只返回当前预算内 best-seen kernel。
- Web V2 是当前源码开发态；V1.0.0-rc1 wheel 不应被理解为已经包含全部最新 Web 能力。
- profiler 不可用或证据不足时，诊断会退化为 benchmark/log based diagnosis，缺失字段保持 `null`。
- mcProfiler 目前支持读取和解析已有 case/log 产物；不要把合成 fixture 当作真实性能证据。
- Paged Attention C500 PR/样例当前仍是 baseline scaffold；真实采集受 SSHRunner SFTP/session 阻塞影响，尚不能作为端到端性能结论。
- 远程命令必须通过 command guard；被拦截的命令需要改写到安全 workspace 约束内。

## CLI Fallback

文件驱动模式仍保留，适合开发者调试和回归验证，不是普通用户主入口。

V2 run request + settings:

```bash
cp examples/settings.yaml.example settings.yaml
python main.py --run-request examples/run_request.yaml --settings settings.yaml
```

传统 V1 config:

```bash
python main.py --config kernel_opt_agent/config.example.yaml
```

安装为包后也可以使用 console script:

```bash
tilelang-agent --config config.yaml
```

开发者模式可以从 Web 页面导出 `run_request.yaml` 和 `settings.yaml`，其中 settings 仍只能包含环境变量名和非密钥配置。

## 安装包与 Release

当前 `pyproject.toml` 版本是 `1.0.0rc1`。已有 GitHub Release 是 V1.0.0-rc1 预发布包，定位是 V1 MVP，不代表当前 `v2/evidence-guided-agent` 分支的全部 Web V2 开发态能力已经发布到 wheel。

- [V1.0.0-rc1 Release](https://github.com/cc-c122/TileLang-Remote-Autotuning-Agent/releases/tag/v1.0.0-rc1)
- [wheel](https://github.com/cc-c122/TileLang-Remote-Autotuning-Agent/releases/download/v1.0.0-rc1/tilelang_remote_autotuning_agent-1.0.0rc1-py3-none-any.whl)
- [source archive](https://github.com/cc-c122/TileLang-Remote-Autotuning-Agent/releases/download/v1.0.0-rc1/tilelang_remote_autotuning_agent-1.0.0rc1.tar.gz)

本地开发安装：

```bash
pip install -e .
```

wheel 安装：

```bash
pip install dist/*.whl
```

## 目录结构

```text
.
  README.md
  instruction.md
  pyproject.toml
  examples/
  tests/
  kernel_opt_agent/
    main.py
    server/
      app.py
      models.py
      task_manager.py
      job_worker.py
      run_request_builder.py
      settings_store.py
    frontend/
      index.html
    runner/
      local_runner.py
      ssh_runner.py
      command_guard.py
    hardware/
    profiler/
    diagnosis/
    patcher/
    storage/
    samples/
    workspace/
```

## 开发自检

提交前建议运行：

```bash
python -m compileall -q kernel_opt_agent tests
python -m unittest discover -s tests
git diff --check
```

不要提交真实 settings、SSH key、API key、token、password、本地 workspace 结果或远程实验日志。
