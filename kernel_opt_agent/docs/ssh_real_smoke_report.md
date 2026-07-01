# SSH Real Smoke Report

## Status

SSH real smoke was attempted with `python main.py --config ssh.config.yaml`, but did not complete because no reachable key-auth SSH target was available from this workspace.

Failure type: SSH connection failure.

## Sanitized Remote Summary

- Host: local SSH config alias resolving to a private LAN address
- Port: 22
- User: local SSH config user
- Auth: SSH key auth requested
- Key path: no usable private key was present under the local `.ssh` directory
- Remote workspace: `/tmp/kernel_opt_workspace_real_smoke`

No passwords, tokens, private keys, or concrete key material are recorded in this report.

## Commands

```bash
pip install -r kernel_opt_agent/requirements.txt
python main.py --config ssh.config.yaml
```

## Observed Result

OpenSSH batch-mode probing failed before upload/remote execution could be validated:

```text
ssh: connect to host <private-lan-host> port 22: Connection timed out
```

Additional environment checks:

- `ssh-agent` was unavailable.
- Docker CLI was installed, but Docker daemon was not running, so a disposable SSH container could not be started.
- No local `sshd` service was available for loopback smoke testing.

The agent run still completed without a traceback and recorded structured failures:

- `summary.csv`: 2 rows
- `experiments.jsonl`: 2 rows
- `failed_cases.jsonl`: 2 rows
- status/category: `ssh_connection_failed`
- error message: `timed out`

Generated local result files:

- `experiments.jsonl`
- `summary.csv`
- `failed_cases.jsonl`
- `all_results.csv`
- `best_kernel.py`
- `best_config.yaml`
- `report.md`

No concrete tokens, passwords, or private key material were found by scanning generated result files for common secret patterns.

## Not Validated

- SSH key auth success
- SFTP sample directory upload
- Rendering only `kernel.entry_file` on the uploaded sample
- Running correctness/build/benchmark under `remote.remote_workspace`
- Pulling back remote `results/`, `logs/`, `best_kernel.py`, `best_config.yaml`, and `report.md`

## Local Baseline

The local mock path remains the fallback validation path for this branch:

```bash
python -m unittest discover -s tests
python main.py --config kernel_opt_agent/config.example.yaml
```

Expected local result files:

- `experiments.jsonl`
- `summary.csv`
- `failed_cases.jsonl`
- `all_results.csv`
- `best_kernel.py`
- `best_config.yaml`
- `report.md`
