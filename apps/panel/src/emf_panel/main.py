from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from emf_shared.db import init_db
from emf_shared.logging import configure_logging
from emf_shared.middleware import TraceIDMiddleware
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.sessions import SessionMiddleware

from .auth import configure_oauth
from .routes import _http_client, router
from .settings import get_settings

configure_logging("panel")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    import redis.asyncio as aioredis

    settings = get_settings()
    init_db(settings.database_url)
    configure_oauth(settings)
    _app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await _app.state.redis.aclose()
    await _http_client.aclose()


app = FastAPI(title="EMF Conduct Panel", lifespan=lifespan)

_DEV_KEY = "dev-session-key-replace-in-prod"  # noqa: S105
_session_secret = os.environ.get("SECRET_KEY", _DEV_KEY)
_is_local_dev = os.environ.get("LOCAL_DEV", "").lower() in ("1", "true", "yes")
if not _is_local_dev and _session_secret == _DEV_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set or is the default development value. "
        "Set a strong SECRET_KEY in .env before starting in production."
    )
if not _is_local_dev and len(_session_secret) < 32:
    raise RuntimeError(
        f"SECRET_KEY is too short ({len(_session_secret)} chars). "
        "Use at least 32 characters for production."
    )
app.add_middleware(TraceIDMiddleware, service_name="panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    https_only=not _is_local_dev,
    same_site="lax",
)

_static_dir = Path(__file__).parent.parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
app.include_router(router)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
