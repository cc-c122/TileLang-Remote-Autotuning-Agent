from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path


DEFAULT_HOST_KEYS = Path(__file__).resolve().parents[1] / "workspace/server_runtime/host_keys.json"


class HostKeyError(ValueError):
    def __init__(self, code: str, fingerprint: str | None = None):
        super().__init__(code)
        self.code = code
        self.fingerprint = fingerprint


def key_fingerprint(key) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class HostKeyStore:
    _lock = threading.RLock()

    def __init__(self, path: Path = DEFAULT_HOST_KEYS):
        self.path = path

    def _key(self, host: str, port: int) -> str:
        return hashlib.sha256(f"{host.strip().lower()}:{port}".encode()).hexdigest()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
                raise ValueError()
            return data
        except (OSError, ValueError) as exc:
            raise HostKeyError("host_key_store_invalid") from exc

    def verify(self, host: str, port: int, observed: str, confirmed: str | None) -> None:
        with self._lock:
            known = self._load().get(self._key(host, port))
            if known is not None:
                if known != observed:
                    raise HostKeyError("host_key_changed")
            elif confirmed is None:
                raise HostKeyError("host_key_confirmation_required", observed)
            elif confirmed != observed:
                raise HostKeyError("host_key_mismatch")

    def remember(self, host: str, port: int, observed: str, confirmed: str | None) -> None:
        with self._lock:
            self.verify(host, port, observed, confirmed)
            values = self._load()
            values[self._key(host, port)] = observed
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
