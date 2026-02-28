from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from emf_shared.db import init_db

from .auth import configure_oauth
from .routes import router
from .settings import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    init_db(settings.database_url)
    configure_oauth(settings)
    yield


app = FastAPI(title="EMF Conduct Panel", lifespan=lifespan)

_session_secret = os.environ.get("SECRET_KEY", "dev-session-key-replace-in-prod")
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    https_only=False,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
