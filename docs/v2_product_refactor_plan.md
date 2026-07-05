# V2 产品形态重构说明书

## 1. 重构背景

当前仓库的 `v2-file-flow-freeze` 是文件驱动流程：

```bash
python main.py --run-request run_request.yaml --settings settings.yaml
```

这个流程中，前端负责生成 `run_request.yaml` 和 `settings.yaml`，用户再手动运行 CLI。该模式继续保留，作为 fallback / advanced mode / debug mode，但它不再是 V2 的主要产品形态。

新的 V2 产品目标是：

> 用户不需要接触 `config.yaml`、`run_request.yaml`、`settings.yaml`，也不需要手动运行 CLI。用户只通过 Web UI 创建任务、启动优化、查看进度和结果。

## 2. V2 主产品流程

用户只需要在 Web UI 中：

1. 粘贴或上传 TileLang sample。
2. 输入或选择 GPU 型号。
3. 确认自动补全的硬件参数。
4. 使用长期保存的 SSH 设置和 LLM API 设置。
5. 点击“开始优化”。

系统自动完成：

1. 生成内部 `run_request`。
2. 生成脱敏 `effective_config`。
3. 上传 sample 到远程 SSH workspace。
4. 自动运行 build / correctness / benchmark。
5. 自动搜索参数或生成受控 patch。
6. 自动记录每轮结果。
7. 最终返回 best kernel、best config、性能提升比例和 report。

## 3. 必须保留的当前能力

以下能力不得删除：

1. CLI 仍然支持：

```bash
python main.py --run-request run_request.yaml --settings settings.yaml
```

2. `run_request.yaml` / `settings.yaml` 作为高级模式和调试模式保留。
3. 现有 runner、search policy、hardware detection、safe probe、report writer 不删除。
4. 现有结果文件继续作为底层产物保留：
   - `experiments.jsonl`
   - `summary.csv`
   - `report.md`
   - `best_kernel.py`
   - `best_config.yaml`
   - `failed_cases.jsonl`
   - `profiler_results.jsonl`
   - `diagnosis.jsonl`
   - `patch_trials.jsonl`

## 4. 新增 Backend HTTP API

新增一个轻量后端服务。V2 产品化后端直接使用 FastAPI，不再使用标准库 HTTP server 作为主实现。

新增目录：

```text
kernel_opt_agent/server/
  app.py
  models.py
  task_manager.py
  settings_store.py
  run_request_builder.py
  job_worker.py
```

启动命令：

```bash
python -m kernel_opt_agent.server.app
```

第一阶段先用 local runner 跑通 Web API 闭环，再接 SSH remote runner。也就是说，V2.1 的验收重点是 API、任务状态、结果读取和 UI 闭环真实可用；V2.2 再把同一套 task flow 接到远程 SSH 容器。

### 4.1 Settings API

#### POST `/api/settings`

保存长期设置，包括：

1. SSH host
2. SSH port
3. username
4. auth_type
5. password_env 或 key_path
6. remote_workspace
7. LLM provider
8. LLM base_url
9. LLM model
10. LLM api_key_env

安全要求：

1. 不保存明文 password。
2. 不保存明文 API key。
3. 不保存私钥内容。
4. 只保存环境变量名或 key path。

#### GET `/api/settings`

读取脱敏后的长期设置。

### 4.2 Hardware Resolve API

#### POST `/api/hardware/resolve`

输入：

1. `gpu_model`
2. optional `backend`
3. optional `user_overrides`

输出：

1. 自动补全的 `hardware_profile`
2. 每个字段的 `source`
3. 每个字段的 `confidence`
4. `unknown_fields`

硬件字段包括：

1. GPU 名称
2. backend
3. SM 数量
4. warp size
5. max threads per block
6. shared memory per block
7. registers per thread
8. HBM bandwidth
9. tensor core / MMA 支持
10. dtype 支持

解析优先级：

```text
user_overrides > remote detection > builtin profile > LLM lookup > safe probe > unknown
```

要求：

1. LLM 自动补全必须标注 `source=llm`。
2. LLM 自动补全的 `confidence` 不能默认 high。
3. 无法确认的字段保持 `null`。
4. 不允许伪造硬件参数。
5. LLM lookup 允许作为硬件参数补全来源。
6. 联网搜索官方文档必须是可选开关，默认不开或按配置明确开启。

### 4.3 Task API

#### POST `/api/tasks`

创建并启动优化任务。

请求体包括：

1. `project_name`
2. `sample_source_type: inline | upload | path`
3. `sample_inline_text`
4. `sample_upload_id`
5. `entry_file`
6. `gpu_model`
7. `hardware_profile`
8. `hardware_overrides`
9. `build_command`
10. `correctness_command`
11. `benchmark_command`
12. `search_budget`
13. `objective`
14. `patching_enabled`
15. `profiler_enabled`

后端行为：

1. 保存 sample 到 `workspace/tasks/{task_id}/sample/`。
2. 根据用户输入和长期 settings 生成内部 `run_request`。
3. 生成 `effective_config`。
4. 启动后台 `job_worker`。
5. 返回 `task_id`。

#### GET `/api/tasks/{task_id}`

返回任务状态：

1. `pending`
2. `running`
3. `completed`
4. `failed`
5. `cancelled`

返回当前摘要：

1. `current_iteration`
2. `total_trials`
3. `best_latency`
4. `baseline_latency`
5. `improvement_percent`
6. `current_stage`
7. `latest_message`

#### GET `/api/tasks/{task_id}/events`

返回任务事件流。V2 第一阶段可以先实现 polling JSON，后续再做 SSE / WebSocket。

事件包括：

1. `task_created`
2. `hardware_resolved`
3. `sample_uploaded`
4. `build_started`
5. `correctness_started`
6. `benchmark_started`
7. `trial_completed`
8. `best_updated`
9. `patch_generated`
10. `report_generated`
11. `task_completed`
12. `task_failed`

#### POST `/api/tasks/{task_id}/cancel`

取消任务。

要求：

1. 如果任务仍在 `pending` 或 `running`，应尽力停止后续 trial。
2. 已经启动的单条 build / correctness / benchmark 命令不要求强杀第一版实现，但必须设置取消标记，当前命令结束后不再继续提交新 trial。
3. 任务状态更新为 `cancelled`。
4. 写入 `task_cancelled` 事件。
5. 已产生的结果文件继续保留，前端可以查看 partial results。

#### GET `/api/tasks/{task_id}/results`

返回最终结果：

1. `best_kernel`
2. `best_config`
3. `report_markdown`
4. `summary_table`
5. `improvement_percent`
6. `failed_cases`
7. `generated_files`

#### GET `/api/tasks/{task_id}/download/best_kernel`

下载 `best_kernel.py`。

#### GET `/api/tasks/{task_id}/download/report`

下载 `report.md`。

## 4.4 V2.2 Remote SSH Task Flow

V2.1 先用 `runner.type: local` 跑通 Web API 闭环；V2.2 在同一套 task flow 上接入 `runner.type: ssh`，让 Web UI 创建的任务能够自动上传 sample 到远程 SSH workspace 并执行 build / correctness / benchmark。

### runner.type local / ssh

`runner.type` 决定任务执行位置：

1. `local`：用于 V2.1 API、任务状态、结果读取和 UI 闭环验证。任务在本机 workspace 中执行，不要求 SSH 设置。
2. `ssh`：用于 V2.2 远程容器执行。任务使用长期 settings 中的 SSH 配置，后端将 sample 上传到 `remote_workspace`，并在远程 workspace 中运行命令。

Web UI 不应让用户手写 `run_request.yaml` 或 `settings.yaml`。后端内部仍可生成等价的 run_request / effective_config，并保存到 task workspace 方便审计。

### settings/test-connection

V2.2 需要新增 settings 连接测试能力，建议接口：

```text
POST /api/settings/test-connection
```

输入使用当前 settings 表单内容或已保存 settings。测试内容：

1. 校验 `runner.type` 是否为 `ssh`。
2. 校验 SSH host、port、username、auth_type、password_env / key_path、remote_workspace 是否完整。
3. 对 password auth，确认 `password_env` 对应的环境变量在后端进程环境中存在，但不返回真实值。
4. 建立 SSH 连接。
5. 检查 `remote_workspace` 是否为允许的绝对路径，并确认当前用户有读写权限。
6. 可选执行低风险探测命令，例如 `pwd`、`python --version`，仍必须经过 command guard 或等价 allowlist。

返回值必须脱敏：

```yaml
status: ok        # ok | failed
auth_type: password
password_env_present: true
remote_workspace_writable: true
message: connected
```

失败时只返回原因类别和脱敏说明，不返回 password、API key、token、私钥内容或完整敏感环境变量值。

### password_env 规则

V2.2 继续使用 password auth 优先的远程连接方式，但明文密码永远不能进入 settings、run_request、effective_config、日志、事件流、报告或前端响应。

规则：

1. settings 只保存 `password_env`，例如 `KERNEL_AGENT_SSH_PASSWORD`。
2. 后端从自身进程环境读取 `password_env` 指向的真实密码。
3. 如果 `auth_type: password`，`password_env` 必填，且 test-connection / task start 时必须检查该环境变量是否存在。
4. 如果环境变量缺失，任务不得启动，返回脱敏错误，例如 `SSH password env var is not set: KERNEL_AGENT_SSH_PASSWORD`。
5. API 响应只能返回 `password_env` 名称和 `password_env_present: true/false`，不能返回真实密码。
6. key auth 可保留，但私钥内容不能写入 settings；只允许保存 `key_path`。

### remote_workspace 规则

`remote_workspace` 是远程容器内每个任务执行的根路径。V2.2 必须把它当作高风险路径处理。

规则：

1. `remote_workspace` 必须是远程机器上的绝对路径。
2. 禁止使用 `/`、`/bin`、`/boot`、`/dev`、`/etc`、`/lib`、`/proc`、`/root`、`/sbin`、`/sys`、`/usr`、`/var` 等系统目录作为 workspace 根。
3. 建议每个 task 使用子目录：`{remote_workspace}/tasks/{task_id}`。
4. 上传 sample、生成候选、运行命令和下载产物都必须限制在 task workspace 内。
5. 清理远程目录前必须确认目标路径位于允许的 task workspace 内。
6. workspace 已存在且非空时，必须有明确 marker 或 task_id 子目录隔离策略，避免误删用户文件。
7. 远程 build / correctness / benchmark 命令仍必须受 `command_guard` 约束。

## 5. 新增 Web UI

当前前端只读结果文件，不符合新的 V2 产品目标。V2 需要新增 Web UI，支持创建任务、启动任务、查看进度和查看结果。

可以先使用纯 HTML/JS，也可以使用 React/Vite。

第一阶段继续增强当前单文件 HTML，不急着迁移 React/Vite。只有当单文件 HTML 已经明显阻碍维护时，再单独立项迁移。

如果使用 React，建议目录：

```text
frontend/
  src/
    pages/
      NewTaskPage.tsx
      SettingsPage.tsx
      TaskDetailPage.tsx
      ResultsPage.tsx
    components/
      SampleEditor.tsx
      HardwareProfileEditor.tsx
      SettingsPanel.tsx
      ProgressTimeline.tsx
      ResultViewer.tsx
```

### 5.1 设置页 SettingsPage

用于长期保存：

1. SSH 设置
2. LLM API 设置

要求：

1. 页面不要求用户每次新建任务都重新填写 SSH 和 LLM API。
2. 这些设置是长期设置。
3. 不保存明文 password / API key。

### 5.2 新建任务页 NewTaskPage

#### A. 输入算子 Sample

支持：

1. 粘贴代码
2. 上传 sample 目录
3. `entry_file` 输入框

不要求用户写 `config.yaml`。

#### B. 选择 GPU 型号

支持：

1. 输入框或下拉框
2. “自动搜索硬件参数”按钮

#### C. 自动补全的硬件参数

用表格展示：

1. 字段
2. 值
3. 单位
4. 来源
5. 置信度

用户可以手动修改字段。用户修改后的字段应标记为：

```text
source = 用户修改
```

#### D. 运行命令

字段：

1. `build_command`
2. `correctness_command`
3. `benchmark_command`

默认值：

```yaml
build_command: python -m py_compile kernel.py
correctness_command: python correctness.py
benchmark_command: python benchmark.py
```

#### E. 搜索预算

字段：

1. `max_iterations`
2. `candidates_per_iteration`
3. `timeout_seconds`
4. `objective: latency | tflops`
5. `patching_enabled`
6. `profiler_enabled`

#### F. 开始优化按钮

点击后调用：

```text
POST /api/tasks
```

### 5.3 任务详情页 TaskDetailPage

展示：

1. 当前状态
2. 当前阶段
3. trial 数量
4. baseline latency
5. best latency
6. 提升比例
7. 实时日志
8. 最新 best config
9. 最新 best kernel 摘要

### 5.4 结果页 ResultsPage

展示：

1. baseline vs best 性能对比
2. 提升百分比
3. best kernel 代码
4. best config
5. `report.md` 渲染
6. 下载按钮

## 6. 内部 Run Request Builder

新增：

```text
kernel_opt_agent/server/run_request_builder.py
```

作用：

> 把 Web UI 请求转换成当前后端已有的 `run_request` / `effective_config`。

要求：

1. 用户不需要知道 `run_request.yaml`。
2. 后端内部仍可复用现有 run_request schema。
3. 生成的 `run_request` 和 `effective_config` 要保存到 task workspace，方便审计。
4. 任务结束后，结果页面可以提供“查看内部配置”按钮，但这不是主流程。

## 7. 默认行为

用户最小输入应该只有：

1. sample
2. GPU 型号
3. 三条命令，允许使用默认值
4. 已保存的 SSH / LLM 设置

如果用户没有提供 `search_space`，系统必须自动生成 `search_space`。

自动生成 `search_space` 的依据：

1. sample 中出现的模板占位符
2. GPU 硬件参数
3. builtin rules
4. LLM 建议
5. safe probe 结果

如果 sample 里没有模板占位符，V2 可以先进入 two modes：

1. 参数搜索模式不可用。
2. patching 模式尝试在受控区域生成优化版本。

如果 patching 也不可用，则提示用户：

```text
当前 sample 没有可调参数或 patch region，系统只能运行 baseline benchmark。
```

## 8. 安全要求

1. Web 后端不允许暴露任意 shell 执行接口。
2. 用户输入的命令仍必须经过 `command_guard`。
3. SSH password 和 LLM API key 不保存明文。
4. 后端日志、结果文件、前端展示都必须脱敏。
5. 每个任务有独立 workspace。
6. 每个任务支持 timeout。
7. 任务失败不能导致服务崩溃。
8. 远程 workspace 清理必须受安全检查保护。

## 9. 验收标准

完成后应满足：

1. 用户打开 Web UI。
2. 在设置页保存 SSH 和 LLM 配置。
3. 在新建任务页粘贴 sample。
4. 输入 GPU 型号。
5. 点击“自动搜索硬件参数”后生成可编辑 hardware profile。
6. 点击“开始优化”。
7. 后端自动生成内部 `run_request` / `effective_config`。
8. 后端通过 SSH 自动运行任务。
9. 前端显示任务进度。
10. 任务结束后，前端展示 best kernel、baseline latency、best latency、提升比例和 report。
11. 用户不需要手写 `config.yaml`、`run_request.yaml`、`settings.yaml`。
12. 用户不需要手动运行 CLI。
13. 旧 CLI 文件驱动模式仍然保留。

## 10. 实施分工建议

### 后端线程

1. 新增 `kernel_opt_agent/server/`。
2. 实现 settings store。
3. 实现 `/api/hardware/resolve`。
4. 实现 `run_request_builder.py`。
5. 实现 task manager / job worker。
6. 实现 `/api/tasks`、`/api/tasks/{task_id}`、events、results、download API。
7. 复用现有 runner/search/report 能力，不重写已有优化主流程。
8. 保证每个 task 有独立 workspace。

### 前端线程

1. 把当前“生成任务文件”的主流程升级为“创建并启动优化任务”。
2. 新增设置页，调用 `/api/settings`。
3. 新增硬件参数补全，调用 `/api/hardware/resolve`。
4. 新增任务详情页，轮询 task status / events。
5. 新增结果页，调用 task results / download API。
6. 保留文件驱动模式入口，作为高级模式。

### 文档线程

先暂停大改。等后端和前端 API 稳定后，再统一更新中文 README。

## 11. 已确认架构决策

1. 后端主实现直接使用 FastAPI，不使用标准库 HTTP server 作为 V2 产品化主路径。
2. 前端第一阶段继续增强当前单文件 HTML，不急着迁移 React/Vite。
3. 第一阶段先用 local runner 跑通 Web API 闭环，再接 SSH remote runner。
4. `/api/hardware/resolve` 允许使用 LLM lookup，但必须标注字段来源和置信度。
5. 联网搜索官方文档作为可选开关，不作为默认必需能力。
6. 必须新增 `POST /api/tasks/{task_id}/cancel`，并支持 `cancelled` 状态和 partial results 查看。
