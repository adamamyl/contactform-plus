from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from emf_shared.tracing import TRACE_HEADER, new_trace_id, set_trace_id


class TraceIDMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, service_name: str) -> None:
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(TRACE_HEADER, "")
        trace_id = incoming if incoming else new_trace_id()
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response
