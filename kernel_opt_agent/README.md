# TileLang Remote Autotuning Agent MVP

V1 is a safe, reproducible backend loop for template-based TileLang kernel tuning. It does not claim global optimality; it returns the best-seen kernel within the configured search budget.

## Run the local mock

Install dependencies first:

```bash
pip install -r kernel_opt_agent/requirements.txt
```

```bash
python main.py --config kernel_opt_agent/config.example.yaml
```

The mock sample needs no GPU or SSH server. Results are written under:

```text
kernel_opt_agent/workspace/results/
```

Important outputs:

- `experiments.jsonl`
- `summary.csv`
- `failed_cases.jsonl`
- `all_results.csv`
- `best_kernel.py`
- `best_config.yaml`
- `report.md`

## View Results Locally

The V1 frontend is a read-only local result viewer. It does not call a backend HTTP API, execute commands, read credentials, or modify files under `workspace/results/`.

Open it in either of these ways:

- Open `kernel_opt_agent/frontend/index.html` in a Chromium-based browser and click "选择结果目录", then select `kernel_opt_agent/workspace/results/`.
- Or serve `kernel_opt_agent/` with any static file server and open `/frontend/index.html`; the page will read `../workspace/results/` relative to itself.

If the backend is still running, the viewer shows exactly which expected files are still `waiting`.

## Configuration

Copy `config.example.yaml` to your own `config.yaml` and edit:

- `runner.type`: `local` or `ssh`
- `search.strategy`: `grid`, `random`, `llm`, `rule_based`, or `hybrid`
- `search_space`: required and non-empty
- `kernel.sample_path`: file or directory
- `kernel.entry_file`: only this file is rendered as a template

For SSH key-auth smoke testing, copy `config.ssh.example.yaml`, replace only the host, user, key path, and remote workspace fields, then run:

```bash
python main.py --config path/to/ssh.config.yaml
```

The SSH runner uploads the sample file or directory, renders only `kernel.entry_file`, runs commands under `remote.remote_workspace`, and downloads remote artifacts into `workspace/results/remote_artifacts/` when present.

Template placeholders use `{{BM}}` syntax. Placeholder names must exactly match `search_space` keys, including case. Boolean values render as Python `True` / `False`.

## Security Limits

Commands run in the trial workspace root. V1 does not support per-command working directories. `command_guard.py` blocks dangerous fragments such as `rm -rf`, `mkfs`, `dd if=`, shutdown/reboot commands, package removal, and destructive operations aimed outside the workspace.

API keys, SSH passwords, and tokens must not be written into config files. LLM API keys are read only from `llm.api_key_env`. SSH password auth is validated as configuration but intentionally not implemented in V1; use SSH key auth for remote runs.

## Search Behavior

- `grid`: deterministic enumeration.
- `random`: fixed seed from `search.random_seed`.
- `rule_based`: conservative values from the declared search space.
- `llm`: OpenAI-compatible `/chat/completions`, strict JSON, pydantic validation.
- `hybrid`: tries LLM, falls back to rule-based, then fills with grid/random candidates.

Correctness failures are recorded but excluded from performance ranking. Single-candidate failures do not stop the overall run.
