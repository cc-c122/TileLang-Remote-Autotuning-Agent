from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_CURRENT: ContextVar[Any | None] = ContextVar("ssh_execution_context", default=None)


def current_ssh_context() -> Any | None:
    return _CURRENT.get()


@contextmanager
def bind_ssh_context(context: Any) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)
