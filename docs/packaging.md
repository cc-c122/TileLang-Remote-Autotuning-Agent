# Packaging

本文说明 V1 安装包的构建、验证和发布口径。当前阶段暂不发布到 PyPI，先将 wheel 挂到 GitHub Release。

## Build Wheel

建议在干净虚拟环境中构建：

```bash
python -m pip install --upgrade pip build
python -m build
```

构建完成后应生成：

```text
dist/
  tilelang_remote_autotuning_agent-*.whl
  tilelang_remote_autotuning_agent-*.tar.gz
```

不要把 `dist/`、`build/` 或 `*.egg-info/` 提交到 Git。

## Verify Wheel

在新的虚拟环境中安装 wheel：

```bash
python -m venv .venv-wheel-test
. .venv-wheel-test/bin/activate
pip install dist/*.whl
```

PowerShell：

```powershell
python -m venv .venv-wheel-test
.venv-wheel-test\Scripts\Activate.ps1
pip install dist/*.whl
```

验证 CLI entry point：

```bash
tilelang-agent --help
tilelang-agent --config kernel_opt_agent/config.example.yaml
```

如果从仓库源码目录外验证，需要复制一份配置和 sample，或把 `kernel.sample_path` 改成实际可访问路径。V1 配置中的相对 sample 路径按包内 `kernel_opt_agent/` 目录解析。

## Required Package Contents

wheel 必须包含：

- `kernel_opt_agent` Python package。
- `kernel_opt_agent/config.example.yaml`。
- `kernel_opt_agent/config.ssh.example.yaml`。
- `kernel_opt_agent/samples/**`。
- `kernel_opt_agent/frontend/index.html`。
- `kernel_opt_agent/hardware_profiles/**`。
- console script: `tilelang-agent = kernel_opt_agent.main:main`。

wheel 不应包含：

- `kernel_opt_agent/workspace/**` 运行产物。
- `dist/`。
- `build/`。
- `*.egg-info/`。
- `__pycache__/` 或 `*.pyc`。
- 真实 `config.yaml`、SSH key、password、token 或 API key。

可用以下命令抽查 wheel 内容：

```bash
python -m zipfile -l dist/*.whl
```

重点确认示例配置、mock sample、前端 HTML、硬件 profile 和 entry point metadata 都在包内，同时 workspace 产物不在包内。

## Release Channel

V1 暂不发布 PyPI。发布方式：

1. 本地构建 wheel 和 sdist。
2. 在干净环境中安装 wheel 并运行 CLI smoke。
3. 创建 GitHub Release。
4. 将 `dist/*.whl` 和 `dist/*.tar.gz` 作为 release assets 上传。
5. Release note 中说明当前版本仍是 V1 best-seen autotuning loop，不保证全局最优。

PyPI 发布需要后续单独决策，不属于当前 V1 packaging 范围。
