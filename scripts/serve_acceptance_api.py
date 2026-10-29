"""Run the unchanged Web API with isolated acceptance settings, no user secrets."""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from kernel_opt_agent.main import WORKSPACE_ROOT
from kernel_opt_agent.server import app as server
from kernel_opt_agent.server.job_worker import JobWorker
from kernel_opt_agent.server.settings_store import SettingsStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    state = Path(args.state_dir).resolve()
    state.relative_to(WORKSPACE_ROOT.resolve())
    state.mkdir(parents=True, exist_ok=True)
    settings = state / "settings.yaml"
    server.settings_store = SettingsStore(settings)
    server.worker = JobWorker(server.manager, settings, server.upload_store)
    uvicorn.run(server.app, host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
