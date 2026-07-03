# V1 Password Auth Smoke Report

本报告是后端真实 SSH password auth smoke 的脱敏记录。报告不包含真实密码、真实 token、私有 host、真实端口或私有用户名。

## 运行范围

- Runner: `ssh`
- Auth type: `password`
- Password source: `remote.password_env`
- Sample: `kernel_opt_agent/samples/tilelang_mock_minimal`
- Remote workspace: `<REMOTE_WORKSPACE>`
- Local config: ignored local smoke config, not committed

## 验收结果

| Check | Result |
| --- | --- |
| SSH login via password auth | PASS |
| SFTP sample upload | PASS |
| Commands executed under `remote.remote_workspace` | PASS |
| Correctness ran before benchmark | PASS |
| Benchmark latency parsed | PASS |
| `best_kernel.py` generated | PASS |
| `effective_config.yaml` has no password value | PASS |
| logs / JSONL / CSV / report have no password value | PASS |

## 结果摘要

- `benchmark_ok` candidates: 4
- failed candidates: 0
- best latency: 3.3333 ms
- best TFLOPS: 60.0
- best bandwidth: 420.0 GB/s

## 脱敏说明

真实 smoke 的 `effective_config.yaml` 中包含真实 host、port 和 username，因此这些字段不得复制到文档、PR 描述或评论中。对外文档只保留 `<SSH_HOST>`、`<SSH_PORT>`、`<SSH_USER>` 和 `<REMOTE_WORKSPACE>` 这类占位符。

允许公开的结论只有：

1. password auth 主路径已完成真实 smoke。
2. 密码通过环境变量读取。
3. 远程命令在 workspace 下执行。
4. 运行产物未发现密码值。

## 常见失败覆盖

本次成功路径没有触发失败；文档中的排障章节仍覆盖以下 V1 必查失败：

1. `remote.password_env` 对应环境变量缺失。
2. SSH 认证失败。
3. workspace 非空但没有 `.kernel_opt_agent_workspace` marker。
4. command guard 拦截危险命令。
