from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-router-tests-0000")
os.environ.setdefault("CONFIG_PATH", "tests/fixtures/config.json")

import json
import pathlib

# Write a minimal config.json fixture if it doesn't exist
_fixture_dir = pathlib.Path("tests/fixtures")
_fixture_dir.mkdir(exist_ok=True)
_fixture_path = _fixture_dir / "config.json"
if not _fixture_path.exists():
    _fixture_path.write_text(json.dumps({
        "events": [
            {
                "name": "emfcamp2026",
                "start_date": "2026-05-28",
                "end_date": "2026-05-31",
                "signal_mode": "fallback_only",
                "dispatcher_emails": ["conduct@emfcamp.org"],
            }
        ],
        "conduct_emails": ["conduct@emfcamp.org"],
        "smtp": {
            "host": "localhost",
            "port": 587,
            "from_addr": "conduct@emfcamp.org",
        },
        "panel_base_url": "http://localhost:8001",
    }))

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from router.models import CaseAlert
from router.settings import Settings

TEST_SECRET = "test-secret-key-for-router-tests-0000"
TEST_CASE_ID = str(uuid.uuid4())
TEST_NOTIF_ID = uuid.uuid4()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        secret_key=TEST_SECRET,
        smtp_password="",
        config_path=_fixture_path,
    )


@pytest.fixture
def sample_alert() -> CaseAlert:
    return CaseAlert(
        case_id=TEST_CASE_ID,
        friendly_id="alpha-beta-gamma-delta",
        event_name="emfcamp2026",
        urgency="high",
        status="new",
        location_hint="Near stage",
        created_at=datetime.now(tz=UTC),
    )


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    session.execute.return_value = result
    session.get.return_value = None
    return session
