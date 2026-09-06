# HTTP API 参考

> 适用版本：本文描述 [V2 开发分支](https://github.com/cc-c122/TileLang-Remote-Autotuning-Agent/tree/v2/evidence-guided-agent) 的实现。GitHub 默认 `main` 分支保留 V1；使用本文中的 Web API 和模块前，请切换到 `v2/evidence-guided-agent`。本页同步了 V2 文档，运行代码仍以对应分支为准。

FastAPI 应用入口：

```bash
python -m kernel_opt_agent.server.app
```

默认地址为 `http://127.0.0.1:8765`。服务使用 JSON 请求和响应；下载端点除外。

> 安全提示：API 不接受明文 SSH password、LLM API key、token 或私钥内容。设置接口只保存环境变量名或 key path。

## 1. 通用响应与错误

成功响应通常包含：

```json
{
  "ok": true
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 请求成功；部分测试类接口会在 JSON 中返回 `success: false` |
| `404` | task 或下载文件不存在 |
| `422` | Pydantic 校验失败、字段越界或存在未声明字段 |
| `500` | 服务端未处理错误；任务执行失败通常写入 task 状态，不应导致服务崩溃 |

模型默认拒绝额外字段。客户端不应依赖文档未声明的内部字段。

## 2. 健康检查

### `GET /api/health`

```bash
curl http://127.0.0.1:8765/api/health
```

响应：

```json
{
  "ok": true,
  "service": "tilelang-agent",
  "mode": "local-runner"
}
```

`mode` 是当前服务信息，不代表每个任务只能使用 local runner；任务可在请求中选择 `runner.type=ssh`。

## 3. 长期设置

### `POST /api/settings`

保存 SSH 和 LLM 的非密钥设置。

```bash
curl -X POST http://127.0.0.1:8765/api/settings \
  -H "Content-Type: application/json" \
  -d '{
    "ssh": {
      "host": "gpu.example.com",
      "port": 22,
      "username": "root",
      "auth_type": "password",
      "password_env": "KERNEL_AGENT_SSH_PASSWORD",
      "key_path": null,
      "remote_workspace": "/tmp/kernel_opt_workspace"
    },
    "llm": {
      "provider": "openai_compatible",
      "base_url": "https://api.openai.com/v1",
      "model": "gpt-4o-mini",
      "api_key_env": "OPENAI_API_KEY"
    }
  }'
```

约束：

- `auth_type` 只能是 `password` 或 `key`。
- password auth 必须提供 `password_env`。
- key auth 必须提供 `key_path`。
- `password`、`api_key`、`token`、`private_key`、`key_content`、`secret` 和 `*_value` 会被拒绝。
- 返回的 key path 会被脱敏，并附带对应环境变量是否存在的布尔状态。

### `GET /api/settings`

返回脱敏设置：

```bash
curl http://127.0.0.1:8765/api/settings
```

### `POST /api/settings/test-connection`

使用已保存设置测试 SSH 认证和连接。不接受请求体。

```bash
curl -X POST http://127.0.0.1:8765/api/settings/test-connection
```

连接失败也返回 HTTP 200，并在 JSON 中表示：

```json
{
  "ok": true,
  "success": false,
  "failure": true,
  "error_message": "sanitized error",
  "settings": {}
}
```

该端点只验证 SSH client connection，不保证 SFTP subsystem、session channel、远程命令或 GPU workload 一定可用。

## 4. 硬件解析

### `POST /api/hardware/resolve`

请求：

```json
{
  "gpu_model": "MetaX C500",
  "backend": "mxmaca",
  "user_overrides": {
    "max_threads_per_block": 1024
  },
  "allow_llm_lookup": false,
  "allow_web_lookup": false
}
```

响应中的每个字段包含 value、source、confidence 和说明，并列出 `unknown_fields`。

当前 Web API 实际使用 user override 和 builtin profile。`allow_llm_lookup` 与 `allow_web_lookup` 只产生未使用 warning，不会真的联网查询；未知值保持 `null`。

## 5. 创建任务

### `POST /api/tasks`

最小 inline 示例：

```bash
curl -X POST http://127.0.0.1:8765/api/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "local-demo",
    "runner": {"type": "local"},
    "sample": {
      "source_type": "inline",
      "inline_text": "BM = {{BM}}\n",
      "entry_file": "kernel.py"
    },
    "target": {
      "gpu_model": "unknown",
      "backend": "unknown",
      "user_overrides": {},
      "allow_llm_lookup": false,
      "allow_web_lookup": false
    },
    "commands": {
      "build_command": "python -m py_compile kernel.py",
      "correctness_command": "python -c \"print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"",
      "benchmark_command": "python -c \"print('BENCHMARK_RESULT latency_ms=1.0 tflops=1.0 bandwidth_gbps=1.0')\""
    },
    "budget": {
      "strategy": "rule_based",
      "max_iterations": 1,
      "candidates_per_iteration": 1,
      "timeout_seconds": 60,
      "objective": "latency"
    },
    "profiler": {"enabled": true, "type": "dummy"},
    "patching": {"enabled": false, "run_controlled_trial": false}
  }'
```

Web API 当前约束：

| 字段 | 可用值 |
| --- | --- |
| `runner.type` | `local`, `ssh` |
| `sample.source_type` | `inline`, `path`, `upload` |
| `budget.strategy` | `rule_based` |
| `budget.objective` | `latency` |
| `profiler.type` | `dummy` |

说明：

- inline 必须提供非空 `inline_text`。
- path/upload 必须提供 `path`；它是后端可访问路径，不是浏览器 multipart 目录上传。
- `max_iterations >= 1`、`candidates_per_iteration >= 1`、`timeout_seconds >= 1`。
- SSH 任务从长期 settings 读取连接信息。
- 后端创建独立 task workspace，内部生成 run request 和 effective config。

响应：

```json
{
  "ok": true,
  "task_id": "generated-id",
  "task": {
    "status": "pending"
  }
}
```

## 6. 任务列表与状态

### `GET /api/tasks`

返回最近任务的简要列表，包括 id、项目名、状态、时间、性能提升和最新消息。

### `GET /api/tasks/{task_id}`

任务状态：

- `pending`
- `running`
- `completed`
- `failed`
- `cancelled`

摘要字段包括：

- `current_iteration`
- `total_trials`
- `best_latency`
- `baseline_latency`
- `improvement_percent`
- `current_stage`
- `latest_message`

示例：

```bash
curl http://127.0.0.1:8765/api/tasks/<task_id>
```

## 7. 事件

### `GET /api/tasks/{task_id}/events`

当前实现为 polling JSON，不是 SSE/WebSocket。

```bash
curl http://127.0.0.1:8765/api/tasks/<task_id>/events
```

事件至少可能包括 task created/queued、sample uploaded、hardware resolved、correctness/benchmark/profiler started、trial completed、best/report updated，以及 completed/failed/cancelled。

事件用于 UI 进度；准确的 trial 结果应读取结果 API 和 JSONL artifact。

## 8. 取消任务

### `POST /api/tasks/{task_id}/cancel`

```bash
curl -X POST http://127.0.0.1:8765/api/tasks/<task_id>/cancel
```

取消是协作式的：服务设置 `cancel_requested`，worker 在候选边界等检查点停止。已经启动的 subprocess/SSH 命令可能会运行到当前命令返回或 timeout。部分结果仍然保留并可读取。

## 9. 结果

### `GET /api/tasks/{task_id}/results`

主要字段：

```text
task
results.best_kernel
results.best_config
results.report_markdown
results.summary_table
results.improvement_percent
results.failed_cases
results.generated_files
results.evidence_summary
results.diagnoses
results.profiler_status
results.profiler_available
```

`profiler_status` 可能为：

- `disabled`
- `collection_failed`
- `benchmark_only`
- `profiler_metrics_available`

重要语义：

- `profiler_available=true` 只表示存在可用的 profiler evidence，不代表所有 trial 或 shape 都有 profiler。
- API 客户端不得把任务级 profiler 状态推断为单个 shape 状态。
- API 客户端不得从 `benchmark_ok` 推断未显式返回的 correctness。
- 缺失字段显示“未采集”，不得用猜测值补齐。

## 10. 下载

### `GET /api/tasks/{task_id}/download/best_kernel`

下载 `best_kernel.py`。

### `GET /api/tasks/{task_id}/download/report`

下载 `report.md`。

文件未生成时返回 404。

## 11. 数据与密钥边界

- 服务设置默认保存在 `kernel_opt_agent/workspace/server_settings.yaml`。
- 任务保存在 `kernel_opt_agent/workspace/tasks/{task_id}`。
- 这些 workspace 产物不应提交到 Git。
- API 返回、日志、effective config 和结果文件均不得包含明文 secret。
- 当前服务没有内置身份认证或多租户隔离，默认只应监听 loopback；对外部署前必须增加反向代理认证、TLS、访问控制和任务配额。

## 12. OpenAPI

FastAPI 启动后可通过以下地址查看自动生成文档：

```text
http://127.0.0.1:8765/docs
http://127.0.0.1:8765/redoc
http://127.0.0.1:8765/openapi.json
```

自动文档反映 Pydantic 请求 schema；本文件补充安全语义、任务生命周期和当前产品边界。
