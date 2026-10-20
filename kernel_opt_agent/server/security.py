from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_loopback_host(host: str) -> str:
    normalized = (host or "").strip().lower()
    if normalized not in LOOPBACK_HOSTS:
        raise ValueError("local API must bind to loopback host only")
    return normalized


async def local_request_only(request: Request, call_next):
    host = (request.url.hostname or "").lower()
    peer = request.client.host if request.client else ""
    test_transport = peer == "testclient" and host == "testserver"
    try:
        local_peer = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local_peer = peer in {"localhost", "testclient"}
    if not local_peer or (host not in LOOPBACK_HOSTS and not test_transport):
        return JSONResponse(status_code=403, content={"detail": "local requests only"})
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            same_origin = parsed.scheme == request.url.scheme and parsed.hostname == request.url.hostname and (parsed.port or 80) == (request.url.port or 80)
        except ValueError:
            same_origin = False
        if not same_origin:
            return JSONResponse(status_code=403, content={"detail": "request origin is not allowed"})
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"detail": "cross-site requests are not allowed"})
    return await call_next(request)


async def safe_validation_error(request: Request, exc: RequestValidationError):
    # Never serialize Pydantic's input/context, which may contain the entire credential body.
    errors = []
    for error in exc.errors():
        location = error.get("loc", ())
        field = location[-1] if location else None
        message = f"{field} must be >= 1" if field in {"max_iterations", "candidates_per_iteration", "timeout_seconds"} else "invalid request field"
        for budget_field in ("max_iterations", "candidates_per_iteration", "timeout_seconds"):
            known_message = f"{budget_field} must be >= 1"
            if known_message in str(error.get("msg", "")):
                message = known_message
        for known_message in (
            "sample.inline_text and sample.path must be omitted for upload samples",
            "sample.path and sample.upload_id must be omitted for inline samples",
            "sample.inline_text and sample.upload_id must be omitted for path samples",
            "device_id requires an SSH session",
            "ssh_session_id requires runner.type=ssh",
        ):
            if str(error.get("msg", "")) == "Value error, " + known_message:
                message = known_message
        errors.append({"type": error.get("type", "validation_error"), "msg": message})
    return JSONResponse(status_code=422, content={"detail": errors})
