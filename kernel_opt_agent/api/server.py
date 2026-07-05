from __future__ import annotations

import argparse
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from kernel_opt_agent.main import RESULTS_DIR, run
from kernel_opt_agent.run_request import (
    USER_SETTINGS_PATH,
    RunRequest,
    _normalize_user_settings,
    _reject_plaintext_secrets,
    app_config_from_run_request,
)


MAX_BODY_BYTES = 2 * 1024 * 1024


@dataclass
class RunState:
    run_id: str
    status: str = "queued"
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    results_dir: str = str(RESULTS_DIR)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results_dir": self.results_dir,
        }


@dataclass
class ApiState:
    runs: dict[str, RunState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, text: str, filename: str | None = None) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    if filename:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    if length > MAX_BODY_BYTES:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def save_settings(settings: dict[str, Any], settings_path: Path = USER_SETTINGS_PATH) -> Path:
    _reject_plaintext_secrets(settings, "user settings")
    normalized = _normalize_user_settings(settings)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(yaml.safe_dump(normalized, sort_keys=True), encoding="utf-8")
    return settings_path


def read_results_snapshot(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    files = [
        "effective_config.yaml",
        "summary.csv",
        "experiments.jsonl",
        "failed_cases.jsonl",
        "profiler_results.jsonl",
        "diagnosis.jsonl",
        "report.md",
        "best_kernel.py",
    ]
    snapshot: dict[str, Any] = {"results_dir": str(results_dir), "files": {}}
    for name in files:
        path = results_dir / name
        if path.exists() and path.is_file():
            snapshot["files"][name] = {"exists": True, "size": path.stat().st_size}
        else:
            snapshot["files"][name] = {"exists": False, "size": 0}
    return snapshot


class LocalApiServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], settings_path: Path = USER_SETTINGS_PATH):
        self.state = ApiState()
        self.settings_path = settings_path
        super().__init__(server_address, LocalApiHandler)


class LocalApiHandler(BaseHTTPRequestHandler):
    server: LocalApiServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except ValueError as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except ValueError as exc:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def _handle_get(self) -> None:
        path = urlparse(self.path).path.strip("/")
        parts = path.split("/") if path else []
        if path == "health":
            json_response(self, HTTPStatus.OK, {"ok": True})
            return
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "status":
            state = self._get_run(parts[1])
            json_response(self, HTTPStatus.OK, {"ok": True, "run": state.to_dict()})
            return
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "results":
            state = self._get_run(parts[1])
            payload = {"ok": True, "run": state.to_dict(), "results": read_results_snapshot(Path(state.results_dir))}
            json_response(self, HTTPStatus.OK, payload)
            return
        if len(parts) == 3 and parts[0] == "runs" and parts[2] in {"best_kernel", "report"}:
            state = self._get_run(parts[1])
            filename = "best_kernel.py" if parts[2] == "best_kernel" else "report.md"
            file_path = Path(state.results_dir) / filename
            if not file_path.exists():
                raise ValueError(f"result file not found: {filename}")
            text_response(self, HTTPStatus.OK, file_path.read_text(encoding="utf-8"), filename)
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def _handle_post(self) -> None:
        path = urlparse(self.path).path.strip("/")
        body = read_json_body(self)
        if path == "settings":
            settings_path = save_settings(body, self.server.settings_path)
            json_response(self, HTTPStatus.OK, {"ok": True, "settings_path": str(settings_path)})
            return
        if path == "runs":
            request = RunRequest.model_validate(body)
            run_id = uuid.uuid4().hex
            state = RunState(run_id=run_id)
            with self.server.state.lock:
                self.server.state.runs[run_id] = state
            thread = threading.Thread(target=self._run_agent, args=(run_id, request), daemon=True)
            thread.start()
            json_response(self, HTTPStatus.ACCEPTED, {"ok": True, "run": state.to_dict()})
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def _get_run(self, run_id: str) -> RunState:
        with self.server.state.lock:
            state = self.server.state.runs.get(run_id)
        if state is None:
            raise ValueError(f"run not found: {run_id}")
        return state

    def _run_agent(self, run_id: str, request: RunRequest) -> None:
        state = self._get_run(run_id)
        with self.server.state.lock:
            state.status = "running"
            state.started_at = utc_now()
        try:
            config = app_config_from_run_request(request, settings_path=self.server.settings_path)
            run(config)
            with self.server.state.lock:
                state.status = "succeeded"
                state.finished_at = utc_now()
        except Exception as exc:
            with self.server.state.lock:
                state.status = "failed"
                state.error = str(exc)
                state.finished_at = utc_now()


def create_server(host: str = "127.0.0.1", port: int = 8765, settings_path: str | Path = USER_SETTINGS_PATH) -> LocalApiServer:
    return LocalApiServer((host, port), Path(settings_path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--settings", default=str(USER_SETTINGS_PATH))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.settings)
    print(f"local API listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
