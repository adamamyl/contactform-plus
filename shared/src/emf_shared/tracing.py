from __future__ import annotations

import uuid
from contextvars import ContextVar

TRACE_HEADER = "X-Trace-ID"
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_var.set(trace_id)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def outbound_headers() -> dict[str, str]:
    """Return X-Trace-ID header dict for outbound httpx calls."""
    tid = get_trace_id()
    return {TRACE_HEADER: tid} if tid else {}
