from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from emf_shared.config import AppConfig, EventConfig, SmtpConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from emf_form.models import Case, IdempotencyToken

from .conftest import make_valid_payload


@pytest.mark.asyncio
async def test_valid_submission_returns_201(client: AsyncClient) -> None:
    payload = make_valid_payload()
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "case_id" in data
    assert "friendly_id" in data


@pytest.mark.asyncio
async def test_honeypot_filled_returns_200(client: AsyncClient) -> None:
    payload = make_valid_payload(website="spam bot")
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "case_id" in data
    assert data["friendly_id"] == "silent-drop"


@pytest.mark.asyncio
async def test_idempotency_token_reuse_returns_200(
    mock_session: AsyncSession, client: AsyncClient
) -> None:
    existing_case_id = uuid.uuid4()
    existing_friendly_id = "word-word-word-word"

    existing_token = MagicMock(spec=IdempotencyToken)
    existing_token.case_id = existing_case_id

    existing_case = MagicMock(spec=Case)
    existing_case.friendly_id = existing_friendly_id

    async def mock_get(model: type, key: object) -> object:
        if model is IdempotencyToken:
            return existing_token
        if model is Case:
            return existing_case
        return None

    mock_session.get = AsyncMock(side_effect=mock_get)  # type: ignore[method-assign]

    payload = make_valid_payload()
    response = await client.post(
        "/api/submit",
        json=payload,
        headers={"X-Idempotency-Key": "test-idempotency-key-123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["friendly_id"] == existing_friendly_id
    assert data["case_id"] == str(existing_case_id)


@pytest.mark.asyncio
async def test_invalid_urgency_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(urgency="critical")
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_what_happened_too_short_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(what_happened="Short")
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_what_happened_too_long_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(what_happened="x" * 10001)
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_health_returns_ok(mock_session: AsyncSession, client: AsyncClient) -> None:
    execute_result = MagicMock()
    mock_session.execute = AsyncMock(return_value=execute_result)  # type: ignore[method-assign]
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["database"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_location_no_fields_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(location={"text": None, "lat": None, "lon": None})
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_phone_with_at_sign_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": "test@example.com",
            "camping_with": None,
        }
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dect_number_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": "test@example.com",
            "phone": "1234",
            "camping_with": None,
        }
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_t9_letters_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": "test@example.com",
            "phone": "ADAM",
            "camping_with": None,
        }
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_international_phone_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": "test@example.com",
            "phone": "+44 7700 900000",
            "camping_with": None,
        }
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_accented_name_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        can_contact=False,
        reporter={
            "name": "Héloïse Müller",
            "pronouns": "sie/ihr",
            "email": None,
            "phone": None,
            "camping_with": "São Paulo crew",
        },
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_accented_text_fields_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        what_happened="naïve résumé — something happened at café de la paix.",
        additional_info="Location: near the crêperie, behind the façade.",
        others_involved="José and Ångström were nearby.",
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_accented_location_text_accepted(client: AsyncClient) -> None:
    payload = make_valid_payload(
        location={"text": "Près du château — zone forêt"},
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


# --- Contact-method validation ---


@pytest.mark.asyncio
async def test_can_contact_yes_no_email_outside_event_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(
        can_contact=True,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": None,
            "camping_with": None,
        },
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_can_contact_yes_phone_only_outside_event_returns_422(client: AsyncClient) -> None:
    payload = make_valid_payload(
        can_contact=True,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": "07700900000",
            "camping_with": None,
        },
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_can_contact_yes_with_email_outside_event_returns_201(client: AsyncClient) -> None:
    payload = make_valid_payload(
        can_contact=True,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": "reporter@example.com",
            "phone": None,
            "camping_with": None,
        },
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_can_contact_no_no_email_returns_201(client: AsyncClient) -> None:
    payload = make_valid_payload(
        can_contact=False,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": None,
            "camping_with": None,
        },
    )
    response = await client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest_asyncio.fixture()
async def event_time_client(mock_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import Settings, get_settings

    today = date.today()
    event_time_config = AppConfig(
        events=[
            EventConfig(
                name="EMF 2026",
                start_date=today,
                end_date=today,
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
    settings = MagicMock(spec=Settings)
    settings.database_url = "postgresql+asyncpg://test:test@localhost/test"
    settings.app_config = event_time_config
    settings.local_dev = False

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: cast(Settings, settings)
    app.dependency_overrides[get_session] = override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_can_contact_yes_phone_only_during_event_returns_201(
    event_time_client: AsyncClient,
) -> None:
    payload = make_valid_payload(
        can_contact=True,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": "1234",
            "camping_with": None,
        },
    )
    response = await event_time_client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_can_contact_yes_no_contact_during_event_returns_422(
    event_time_client: AsyncClient,
) -> None:
    payload = make_valid_payload(
        can_contact=True,
        reporter={
            "name": "Test",
            "pronouns": None,
            "email": None,
            "phone": None,
            "camping_with": None,
        },
    )
    response = await event_time_client.post("/api/submit", json=payload)
    assert response.status_code == 422
