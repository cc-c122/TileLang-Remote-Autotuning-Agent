# TileLang Remote Autotuning Agent

TileLang Remote Autotuning Agent 的产品目标是：让用户在 Web 中粘贴或选择单个 kernel sample 文件、选择 GPU、复用长期保存的 SSH/LLM 设置，点击“开始优化”，系统在受保护的 workspace 内远程运行候选 kernel，并返回当前预算内实际测到的 best-seen kernel。

当前仓库的事实状态要分开看：

- 后端已经提供 FastAPI Web API。
- 前端是 `kernel_opt_agent/frontend/index.html` 单文件页面，调用相对路径 `/api/*`。
- `python -m kernel_opt_agent.server.app` 只启动 API，不会挂载或打开 Web 页面；`GET /api/health` 可用，`GET /` 不是 Web UI。
- 仓库目前没有一条从零可复制的单命令 Web UI 启动路径，也没有内置 CORS/静态文件代理。完整普通用户 Web 快速开始仍是当前缺口。
- 当前唯一完全可复制的运行入口是 CLI fallback；产品设计主入口仍然是 Web。

系统不保证全局最优，只返回当前搜索预算内已经运行并通过 correctness 的 best-seen kernel。

## 快速开始：当前可复制路径

需要 Python 3.10+。

```bash
pip install -r kernel_opt_agent/requirements.txt
cp examples/settings.yaml.example settings.yaml
python main.py --run-request examples/run_request.yaml --settings settings.yaml
```

如果只想启动 API：

```bash
python -m kernel_opt_agent.server.app
```

默认 API 地址：

```text
http://127.0.0.1:8765
```

可验证：

```bash
curl http://127.0.0.1:8765/api/health
```

注意：上面的 API 命令不会提供 `frontend/index.html`。要使用 Web UI，需要现有开发代理或同源托管方式，让页面所在 origin 能访问同源 `/api/*`。不要把“另起一个端口打开静态页面”当成当前可用方案，因为前端没有配置跨域 API base URL。

## Web 当前边界

当前 Web 页面包含“新建任务、任务详情、实验结果、硬件画像、设置、开发者模式”等视图，但普通 Web UI 仍有这些边界：

- sample 支持粘贴，或选择单个文件后读入 textarea，最终作为 inline 内容提交。
- 前端没有 multipart upload、upload id 或目录上传流程。
- `sample.source_type: path/upload` 属于后端 API/高级模式，前提是后端机器能访问该 path；它不是浏览器目录上传。
- Web task 当前固定 `rule_based` 搜索策略和 `latency` 目标。
- Web UI 保存 LLM 设置，但当前 Web task 未接入 LLM 参数规划、硬件查询或 patch 生成。
- Web UI 的 profiler/patch 高级区当前隐藏；普通 Web 请求固定 `profiler.enabled=false`、`patching.enabled=false`。
- Web API model 虽接受 profiler/patching 字段，但 profiler type 目前只允许 `dummy`；不能把它描述成完整 mcProfiler Web 闭环。

## 密钥与环境变量

SSH 密码和 LLM API key 不保存明文。设置文件和服务端保存的配置只记录环境变量名，例如 `KERNEL_AGENT_SSH_PASSWORD` 和 `OPENAI_API_KEY`。`SettingsStore` 会拒绝保存明文 secret 字段，并在返回设置时做脱敏和环境变量存在性提示。

PowerShell:

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = "your-ssh-password"
$env:OPENAI_API_KEY = "your-llm-api-key"
```

bash:

```bash
export KERNEL_AGENT_SSH_PASSWORD="your-ssh-password"
export OPENAI_API_KEY="your-llm-api-key"
```

不要把真实密码、API key、token 或私钥内容写进 README、settings、run request、日志、PR 描述或结果文件。

## API 概览

后端应用标题是 `TileLang Remote Autotuning Agent API`。当前路由包括：

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

## 真实执行流程

核心 trial 的真实顺序是：

1. correctness
2. build
3. benchmark

也就是说，build 只在 correctness 通过后执行；这个顺序不常见，但当前实现就是如此。correctness 失败的候选不会进入 benchmark，也不会参与性能排名。

Web task 创建后，worker 会写入任务 workspace，构造 run request 和 effective config，然后按预算运行候选。候选失败会记录为失败 case，通常不会中断整个任务。

## 搜索、LLM、Profiler 与 Patch

需要区分当前 Web 普通路径和底层 CLI/核心模块能力：

- 当前 Web task 固定 `rule_based`，不会选择 `llm` 或 `hybrid`。
- V1/CLI 核心仍保留 LLM planner 能力；LLM 只能建议参数，不能执行 shell。
- `/api/hardware/resolve` 中的 `allow_llm_lookup` 当前只返回“reserved for later phase and was not used”语义，不会真的联网或调用 LLM 查询硬件。
- 底层核心模块支持 `dummy`、`tilelang_log`、`mxmaca` profiler 路径，但当前普通 Web UI 默认不启用 profiler。
- Web API 当前 profiler type 只允许 `dummy`；不要把 Web 说成已完成真实 mcProfiler 闭环。
- 底层 controlled patch trial 已有验证链和回归测试，但当前普通 Web UI 默认不启用 patch。
- controlled patch 只允许修改显式 patch 区域，并会独立记录到 `patch_trials.jsonl`。
- patch trial 每次都会 rollback，不会更新参数搜索得到的 `best_kernel.py`，也不是 LLM 自动代码优化器。

受控 patch 区域格式：

```python
# BEGIN_AGENT_PATCH: region_name
# patchable code here
# END_AGENT_PATCH
```

patch 校验会拒绝 `subprocess.`、`os.system`、`shell=True`、嵌套 patch 标记等危险片段。

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

## 安全限制

远程命令受 `kernel_opt_agent/runner/command_guard.py` 约束。当前 guard 会拦截高风险片段，例如 `rm -rf`、`mkfs`、`dd if=`、shutdown/reboot、系统包移除、`curl | sh`、`wget | sh`、`chmod 777 /`、跳出 workspace 的 `cd` 和明显针对系统路径的破坏性重定向。

SSH runner 会：

- 在远程 workspace 内执行命令。
- 要求 workspace 是安全路径。
- 使用 `.kernel_opt_agent_workspace` 标记受管目录。
- 拒绝清理未标记的非空 workspace。
- 通过 SFTP 传输 sample 和拉回结果。

settings 只保存非密钥字段和环境变量名；错误信息会尽量脱敏。真实 secret 必须由运行后端的 shell 环境提供。

## 当前限制

- 不保证全局最优，只返回当前预算内 best-seen kernel。
- Web 普通用户主入口仍缺少从零可复制的同源 UI 启动方式；当前 `python -m kernel_opt_agent.server.app` 只启动 API。
- Web V2 是当前源码开发态；V1.0.0-rc1 wheel 不应被理解为已经包含全部最新 Web 能力。
- 当前 Web task 固定 rule-based、dummy/no-profiler、no-patch；LLM planner、mcProfiler、controlled patch 主要属于 CLI/底层模块能力。
- profiler 不可用或证据不足时，诊断会退化为 benchmark/log based diagnosis，缺失字段保持 `null`。
- mcProfiler 目前支持读取和解析已有 case/log 产物；不要把合成 fixture 当作真实性能证据。
- Paged Attention C500 PR/样例当前仍是 baseline scaffold；真实采集受 SSHRunner SFTP/session 阻塞影响，尚不能作为端到端性能结论。
- 远程命令必须通过 command guard；被拦截的命令需要改写到安全 workspace 约束内。

## CLI Fallback

文件驱动模式保留，适合开发者调试和回归验证，也是当前唯一完全可复制的端到端运行入口。

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
git diff --check
python -m compileall -q kernel_opt_agent tests
python -m unittest discover -s tests
```

不要提交真实 settings、SSH key、API key、token、password、本地 workspace 结果或远程实验日志。
