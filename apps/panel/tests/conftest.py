from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

# Set required env vars before importing the app
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-panel-tests-0000")

import pathlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from emf_panel.dispatcher import create_dispatcher_token
from emf_panel.main import app
from emf_panel.settings import Settings


TEST_SECRET = "test-secret-key-for-panel-tests-0000"
TEST_CASE_ID = uuid.uuid4()


_FIXTURE_CONFIG = pathlib.Path(__file__).parent / "fixtures" / "config.json"


def _make_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        secret_key=TEST_SECRET,
        smtp_password="",
        oidc_issuer="http://localhost:9090",
        oidc_client_id="test",
        oidc_client_secret="secret",
        base_url="http://testserver",
        config_path=_FIXTURE_CONFIG,
    )


def _mock_case() -> MagicMock:
    case = MagicMock()
    case.id = TEST_CASE_ID
    case.friendly_id = "alpha-beta-gamma-delta"
    case.urgency = "medium"
    case.status = "new"
    case.assignee = None
    case.location_hint = "Near stage"
    case.tags = []
    case.form_data = {"what_happened": "Something happened", "reporter": {}}
    case.created_at = datetime.now(tz=UTC)
    case.updated_at = datetime.now(tz=UTC)
    return case


@pytest.fixture
def settings() -> Settings:
    return _make_settings()


@pytest.fixture
def valid_token(settings: Settings) -> str:
    return create_dispatcher_token(settings.secret_key, ttl_hours=8)


@pytest.fixture
def expired_token(settings: Settings) -> str:
    return create_dispatcher_token(settings.secret_key, ttl_hours=-1)


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [_mock_case()]
    result.fetchall.return_value = []
    session.execute.return_value = result
    session.get.return_value = _mock_case()
    return session


@pytest.fixture
def conduct_user() -> dict[str, object]:
    return {
        "sub": "user-1",
        "preferred_username": "testuser",
        "email": "test@emfcamp.org",
        "groups": ["team_conduct"],
    }


@pytest.fixture
def regular_user() -> dict[str, object]:
    return {
        "sub": "user-2",
        "preferred_username": "regular",
        "email": "regular@emfcamp.org",
        "groups": [],
    }


@pytest.fixture
async def client(
    mock_session: AsyncMock,
    settings: Settings,
) -> AsyncGenerator[AsyncClient, None]:
    from emf_shared.db import get_session
    from emf_panel.settings import get_settings

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def authed_client(
    mock_session: AsyncMock,
    settings: Settings,
    conduct_user: dict[str, object],
) -> AsyncGenerator[AsyncClient, None]:
    from emf_shared.db import get_session
    from emf_panel.settings import get_settings
    from emf_panel.auth import require_conduct_team

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_conduct_team] = lambda: conduct_user

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
