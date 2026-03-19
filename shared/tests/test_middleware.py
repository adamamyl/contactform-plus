from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from emf_shared.middleware import TraceIDMiddleware
from emf_shared.tracing import TRACE_HEADER, get_trace_id, set_trace_id


def _make_app(service_name: str = "test") -> Starlette:
    captured: dict[str, str] = {}

    async def index(request: Request) -> JSONResponse:
        captured["trace_id"] = get_trace_id()
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/", index)])
    app.add_middleware(TraceIDMiddleware, service_name=service_name)
    app.state.captured = captured
    return app


def test_generates_trace_id_when_none_provided() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    tid = resp.headers.get(TRACE_HEADER, "")
    assert len(tid) == 32
    assert all(c in "0123456789abcdef" for c in tid)


def test_echoes_incoming_trace_id() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/", headers={TRACE_HEADER: "myincomingid"})
    assert resp.headers.get(TRACE_HEADER) == "myincomingid"


def test_trace_id_available_in_route_handler() -> None:
    app = _make_app("svc")
    client = TestClient(app)
    client.get("/", headers={TRACE_HEADER: "routecheck"})
    assert app.state.captured["trace_id"] == "routecheck"


def test_generates_different_id_per_request() -> None:
    app = _make_app()
    client = TestClient(app)
    ids = {client.get("/").headers.get(TRACE_HEADER) for _ in range(5)}
    assert len(ids) == 5


def test_does_not_override_non_empty_incoming_id() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/", headers={TRACE_HEADER: "keepme"})
    assert resp.headers.get(TRACE_HEADER) == "keepme"


def test_trace_id_reset_between_requests() -> None:
    """Each request must get its own trace_id, not bleed from the previous."""
    app = _make_app()
    client = TestClient(app)
    set_trace_id("stale")
    resp1 = client.get("/", headers={TRACE_HEADER: "first"})
    resp2 = client.get("/", headers={TRACE_HEADER: "second"})
    assert resp1.headers.get(TRACE_HEADER) == "first"
    assert resp2.headers.get(TRACE_HEADER) == "second"
