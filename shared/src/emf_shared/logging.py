from __future__ import annotations

import logging

from emf_shared.tracing import get_trace_id

_service_name: str = ""


class _TraceFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.__dict__["trace_id"] = get_trace_id() or "-"
        record.__dict__["service"] = _service_name
        return True


def configure_logging(service_name: str, level: str = "INFO") -> None:
    global _service_name
    _service_name = service_name

    try:
        from pythonjsonlogger.json import JsonFormatter

        fmt: logging.Formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(trace_id)s %(service)s %(message)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
        )
    except ImportError:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s trace=%(trace_id)s %(message)s"
        )

    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    handler.addFilter(_TraceFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
