# V1 RC Checklist

Checked on: 2026-07-03

## Commands

- `git pull`
- `python -m unittest discover -s tests`
- `python -m compileall -q kernel_opt_agent tests`
- `python main.py --config kernel_opt_agent/config.example.yaml`
- `python -m kernel_opt_agent.main --config kernel_opt_agent/workspace/tmp_tilelang_mock_ssh.yaml`

## Results

- Local mock: PASS
- Password SSH smoke: PASS
- Correctness before benchmark: PASS
- Benchmark latency parsed: PASS
- `best_kernel.py` generated: PASS
- No password leak: PASS

## Evidence Summary

- Unit tests: PASS, 38 tests.
- Compile check: PASS for `kernel_opt_agent` and `tests`.
- Local mock run: PASS, completed with `best=52d464ea5df8`.
- Password SSH smoke run: PASS, completed with `best=40fbba2f441e`.
- SSH auth: PASS, password authentication succeeded via `remote.password_env`.
- Upload path: PASS, SFTP sessions opened during the remote run.
- Remote execution path: PASS, candidate stdout reported `CORRECTNESS_CWD` and `BENCHMARK_CWD` under `/tmp/kernel_opt_agent_tilelang_mock_smoke`.
- Execution order: PASS, candidate stdout showed `CORRECTNESS_RESULT` before `BENCHMARK_RESULT`.
- Benchmark parsing: PASS, `summary.csv` included latency values; best latency was `3.3333`.
- Artifacts: PASS, `best_kernel.py`, `best_config.yaml`, `summary.csv`, `all_results.csv`, `experiments.jsonl`, `failed_cases.jsonl`, and `report.md` were generated.
- Secret scan: PASS, password values were not found in `effective_config.yaml`, `agent.log`, `experiments.jsonl`, `failed_cases.jsonl`, `summary.csv`, `all_results.csv`, or `report.md`.

## Known Limitations

- The password SSH smoke used a local ignored config file under `kernel_opt_agent/workspace/`; no real SSH config is committed.
- The minimal TileLang/mock smoke sample is a mock execution path, not a real TileLang GPU kernel validation.
- The smoke target required `python3` on the remote container; configs using `python` may fail if the remote image does not provide a `python` executable.
- This RC check validates password auth, SFTP upload, remote command execution, parsing, and artifact generation on one live container only.
- `failed_cases.jsonl` was empty for the final minimal mock smoke because all candidates passed correctness.
