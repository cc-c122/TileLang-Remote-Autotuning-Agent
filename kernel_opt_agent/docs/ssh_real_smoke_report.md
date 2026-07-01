# SSH Real Smoke Report

## Status

SSH real smoke passed against a local Docker SSH container using SSH key authentication.

## Sanitized Remote Summary

- Host: `127.0.0.1`
- Port: `2222`
- User: `smoke`
- Auth: SSH key auth
- Key path: local-only temporary key; not committed
- Remote workspace: `/tmp/kernel_opt_workspace_success_smoke`
- Runtime: Python `3.11.15` in a disposable Docker container

No passwords, tokens, private keys, or concrete key material are recorded in this report.

## Manual SSH Verification

Command:

```bash
ssh -i <redacted-key-path> -p 2222 smoke@127.0.0.1 'pwd && python3 --version'
```

Result:

```text
/home/smoke
Python 3.11.15
```

## Agent Command

```bash
pip install -r kernel_opt_agent/requirements.txt
python main.py --config ssh.config.yaml
```

`ssh.config.yaml` was copied from `kernel_opt_agent/config.ssh.example.yaml`, filled with local container values, and kept ignored by git.

## Validation Results

- SSH key auth: passed; Paramiko logged publickey authentication success.
- SFTP upload: passed; each trial opened and closed an SFTP session successfully.
- Remote workspace creation: passed; `/tmp/kernel_opt_workspace_success_smoke` existed and was writable.
- Remote workspace marker protection: passed; `.kernel_opt_agent_workspace` existed after the run and was preserved across trial uploads.
- Only `kernel.entry_file` rendered: passed; `kernel.py` had concrete values and no template placeholders.
- Non-entry sample files preserved: passed; remote `correctness.py` and `benchmark.py` SHA256 hashes matched local sample files.
- correctness/build/benchmark execution directory: passed; stdout logs showed `/tmp/kernel_opt_workspace_success_smoke` before command output.
- stdout/stderr local logs: passed; 8 log files were generated for 4 trials.
- Local result artifacts: passed; `best_kernel.py`, `best_config.yaml`, `report.md`, CSV, JSONL, and logs were generated.
- Failure isolation: passed; one correctness-failed candidate was recorded and the run continued.

## Result Counts

- `experiments.jsonl`: 4 rows
- `summary.csv`: 4 rows
- `failed_cases.jsonl`: 1 row
- `logs/*.log`: 8 files

## Best Seen

- Objective: latency
- Best candidate: baseline, iteration `0`, candidate `0`
- Latency: `11.1111 ms`
- TFLOPS: `18.0`
- Bandwidth: `431.5 GB/s`

## Sensitive Information Scan

Generated result files were scanned for:

- `gho_`
- `sk-`
- `password:`
- `token:`
- private-key markers
- local key path fragments

No concrete secrets were found. The generated effective config redacts `remote.key_path` as `<redacted:key_path>` and records only environment variable names for API key/password env fields.

## Code Changes Motivated By Smoke

The first successful SSH run exposed a stale remote `__pycache__` issue: rapid uploads of same-named Python files could allow a correctness check to import an older `kernel.py`. The minimal fix clears the configured remote workspace via SFTP before uploading each trial. This is bounded by remote workspace safety checks, system path prefix rejection, and a required `.kernel_opt_agent_workspace` marker file.

The smoke also showed that writing the local key path into `effective_config.yaml` was unnecessary. `safe_config_dict()` now redacts `remote.key_path`.
