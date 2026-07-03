# V1 Password Auth Smoke

本文记录 V1 SSH password authentication 的真实 smoke 验收口径。文档只保留可复现步骤、检查项和排障说明；不得写入真实密码、真实 token、私有 host、私有端口或任何可识别的容器连接信息。

## 结论

V1 远程 SSH 路径必须支持 password auth。密码只能通过 `remote.password_env` 指向的环境变量传入，不能写入 `config.yaml`、日志、JSONL、CSV、报告、异常堆栈或前端展示。

SSH key auth 可以作为兼容路径保留，但不再是 V1 远程 smoke 的默认路径。

## V1 最小远程运行示例

本次真实 smoke 已确认 password auth、SFTP 上传、远程 workspace 内执行、correctness-before-benchmark、结果拉回和密码不落盘链路可用。对外文档只保留占位符示例：

```yaml
runner:
  type: ssh

remote:
  host: <SSH_HOST>
  port: <SSH_PORT>
  username: <SSH_USER>
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /tmp/kernel_opt_workspace
```

运行前只在本机 shell 设置密码环境变量。

```bash
export KERNEL_AGENT_SSH_PASSWORD='<container-password>'
python main.py --config ssh.config.yaml
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = '<container-password>'
python main.py --config ssh.config.yaml
```

不要把真实密码、真实 host、真实端口或 token 写进配置、文档、日志或 PR 评论。

## Password Auth 配置方式

从 `kernel_opt_agent/config.ssh.example.yaml` 复制一份本地配置，例如：

```bash
cp kernel_opt_agent/config.ssh.example.yaml ssh.config.yaml
```

配置中只保存环境变量名，不保存密码值：

```yaml
runner:
  type: ssh

remote:
  host: <SSH_HOST>
  port: <SSH_PORT>
  username: <SSH_USER>
  auth_type: password
  password_env: KERNEL_AGENT_SSH_PASSWORD
  remote_workspace: /tmp/kernel_opt_workspace
```

要求：

1. `remote.auth_type` 使用 `password`。
2. `remote.password_env` 只写环境变量名。
3. 不允许出现 `remote.password`、`password: <value>`、真实 token 或真实 host。
4. `remote.remote_workspace` 必须是远程容器内绝对路径。

## 环境变量写法

Bash / Linux / macOS：

```bash
export KERNEL_AGENT_SSH_PASSWORD='<container-password>'
```

PowerShell：

```powershell
$env:KERNEL_AGENT_SSH_PASSWORD = '<container-password>'
```

Windows `cmd.exe`：

```bat
set KERNEL_AGENT_SSH_PASSWORD=<container-password>
```

`<container-password>` 是本机 shell 环境变量值，不得提交到仓库，也不得复制到 issue、PR 评论、日志或报告里。

## 模力方舟 Workspace 注意事项

模力方舟容器连接时，先从控制台确认 SSH host、port、username 和登录密码，但这些值不要写进文档或提交记录。配置文件中可以使用本地私有副本保存 host、port、username；密码仍然只能放在环境变量中。

`remote.remote_workspace` 建议使用独立的、仅供本项目管理的目录，例如：

```yaml
remote:
  remote_workspace: /tmp/kernel_opt_workspace
```

workspace 规则：

1. 路径必须是容器内绝对路径。
2. 当前用户必须有读写权限。
3. 不要指向 `/`、`/tmp`、`/home`、`/workspace`、`/usr`、`/etc`、`/var` 等过宽或系统路径。
4. 如果目录非空，必须已经由本项目写入 `.kernel_opt_agent_workspace` marker；否则 SSH runner 会拒绝清理，避免误删用户数据。
5. 若遇到“workspace 非空未标记”，新建一个空目录作为 workspace，或手动确认旧目录只属于本项目后再处理。

## Smoke 执行

确认环境变量已设置后运行：

```bash
python main.py --config ssh.config.yaml
```

成功 smoke 至少应确认：

1. SSH password auth 连接成功。
2. sample 上传到 `remote.remote_workspace`。
3. correctness、build、benchmark 在 `remote.remote_workspace` 下执行。
4. 结果拉回本地 `kernel_opt_agent/workspace/results/`。
5. `effective_config.yaml` 不包含密码值。
6. 日志、JSONL、CSV、报告和 stdout/stderr 文件不包含密码值。

## 如何确认没有泄密

不要把真实密码放进命令历史以外的任何文件。smoke 后可以用脱敏关键词检查输出产物。示例使用占位符；实际执行时只在本机临时 shell 里替换为非公开检查值，不要把真实值写入仓库文件。

检查配置和结果中没有明文字段：

```bash
rg -n "password:|api_key:|token:" ssh.config.yaml kernel_opt_agent/workspace/results
```

检查结果目录没有出现环境变量值。Bash：

```bash
rg -n "$KERNEL_AGENT_SSH_PASSWORD" kernel_opt_agent/workspace/results
```

PowerShell：

```powershell
Get-ChildItem kernel_opt_agent/workspace/results -Recurse -File |
  Select-String -Pattern $env:KERNEL_AGENT_SSH_PASSWORD
```

预期结果：

1. 不应出现真实密码。
2. `effective_config.yaml` 可以出现 `password_env: KERNEL_AGENT_SSH_PASSWORD`。
3. `effective_config.yaml` 不应出现 password 的真实值。
4. 日志中可以出现认证失败类别，但不能出现密码。

## 常见失败

### env 缺失

现象：

```text
SSH password env var is not set: KERNEL_AGENT_SSH_PASSWORD
```

处理：

1. 确认 `remote.password_env` 写的是环境变量名。
2. 在当前 shell 中设置该环境变量。
3. 重新运行 `python main.py --config ssh.config.yaml`。

### 认证失败

现象：

```text
Authentication failed
```

处理：

1. 确认 host、port、username 来自当前容器。
2. 确认密码是当前容器的 SSH 登录密码。
3. 确认没有把密码写进 `config.yaml`。
4. 若容器重建过，重新从控制台获取连接信息。

### workspace 非空未标记

现象：

```text
remote workspace is not marked and is not empty: <remote_workspace>
```

原因：

SSH runner 只会清理带 `.kernel_opt_agent_workspace` marker 的项目管理目录。非空但未标记的目录会被拒绝，防止误删用户文件。

处理：

1. 推荐改用新的空目录，例如 `/tmp/kernel_opt_workspace_<run_id>`。
2. 不要把 workspace 指向系统目录或共享工作目录。
3. 只有在人工确认目录完全归本项目管理后，才允许清理或重新初始化。

### 命令被 guard 拦截

现象：

```text
guard_denied
```

常见原因：

1. 命令包含 `rm -rf`、`mkfs`、`dd if=`、`shutdown`、`reboot`、`apt remove` 等危险片段。
2. 命令尝试写入或破坏 workspace 外路径。
3. 用户把清理、安装、卸载系统包等操作放进 correctness/build/benchmark 命令。

处理：

1. 保持 `build_command`、`correctness_command`、`run_command` 只做项目内构建、正确性检查和 benchmark。
2. 如果需要进入子目录，使用 `cd subdir && python benchmark.py` 这类受控命令。
3. 不要在命令中做系统级安装、卸载或破坏性清理。
