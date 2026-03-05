from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from emf_shared.config import AppConfig, EventConfig, SmtpConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from emf_form.main import app
from emf_form.settings import Settings


@pytest.fixture()
def mock_config() -> AppConfig:
    return AppConfig(
        events=[
            EventConfig(
                name="EMF 2026",
                start_date=date(2026, 7, 12),
                end_date=date(2026, 7, 20),
            )
        ],
        conduct_emails=["conduct@emfcamp.org"],
        smtp=SmtpConfig(
            host="localhost",
            port=587,
            from_addr="conduct@emfcamp.org",
            use_tls=False,
        ),
        panel_base_url="http://localhost:8001",
    )


@pytest.fixture()
def mock_settings(mock_config: AppConfig) -> Settings:
    settings = MagicMock(spec=Settings)
    settings.database_url = "postgresql+asyncpg://test:test@localhost/test"
    settings.app_config = mock_config
    return cast(Settings, settings)


@pytest.fixture()
def mock_session() -> AsyncSession:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session.execute.return_value = execute_result

    return cast(AsyncSession, session)


@pytest_asyncio.fixture()
async def client(
    mock_settings: Settings, mock_session: AsyncSession
) -> AsyncGenerator[AsyncClient, None]:
    from emf_shared.db import get_session

    from emf_form.settings import get_settings

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_session] = override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


def make_valid_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_name": "EMF 2026",
        "outcome_hoped": None,
        "reporter": {
            "name": "Test Person",
            "pronouns": "They/Them/Theirs",
            "email": None,
            "phone": None,
            "camping_with": None,
        },
        "what_happened": "Something bad happened here at the event.",
        "incident_date": "2024-05-30",
        "incident_time": "14:30:00",
        "location": {"text": "Main stage area"},
        "additional_info": None,
        "support_needed": None,
        "urgency": "medium",
        "others_involved": None,
        "why_it_happened": None,
        "can_contact": True,
        "anything_else": None,
        "website": None,
    }
    base.update(overrides)
    return base
