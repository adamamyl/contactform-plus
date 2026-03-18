from __future__ import annotations

import io
import uuid
from collections.abc import AsyncGenerator
from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from emf_shared.config import AppConfig, EventConfig, SmtpConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from emf_form.models import IdempotencyToken
from emf_form.settings import Settings

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

    # session.get returns the token; session.execute used for SELECT (friendly_id)
    mock_session.get = AsyncMock(return_value=existing_token)  # type: ignore[method-assign]

    friendly_id_result = MagicMock()
    friendly_id_result.scalar_one_or_none.return_value = existing_friendly_id
    existing_ids_result = MagicMock()
    existing_ids_result.scalars.return_value.all.return_value = []

    call_count: list[int] = [0]

    async def mock_execute(stmt: object, *args: object, **kwargs: object) -> object:
        call_count[0] += 1
        # First execute is the SELECT (friendly_id) WHERE id = ... for idempotency
        if call_count[0] == 1:
            return friendly_id_result
        # Second execute is SELECT (friendly_id) for collision check
        return existing_ids_result

    mock_session.execute = AsyncMock(side_effect=mock_execute)  # type: ignore[method-assign]

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


# ---------------------------------------------------------------------------
# ClamAV attachment scanning
# ---------------------------------------------------------------------------

_JPEG_HEADER = b"\xff\xd8\xff" + b"\x00" * 9  # minimal valid JPEG magic


@pytest.fixture()
def sb_settings(mock_config: AppConfig) -> Settings:
    settings = MagicMock(spec=Settings)
    settings.database_url = "postgresql+asyncpg://test:test@localhost/test"
    settings.app_config = mock_config
    settings.google_safe_browsing_key = "test-sb-key"
    return cast(Settings, settings)


@pytest_asyncio.fixture()
async def sb_client(
    sb_settings: Settings, mock_session: AsyncSession
) -> AsyncGenerator[AsyncClient, None]:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import get_settings

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: sb_settings
    app.dependency_overrides[get_session] = override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_upload_clean_file_accepted(
    mock_settings: Settings, mock_session: AsyncSession
) -> None:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import get_settings

    mock_settings.attachment_dir = MagicMock()  # type: ignore[assignment]
    mock_settings.attachment_dir.__truediv__ = MagicMock(return_value=MagicMock())

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_session] = override_session

    with patch("emf_form.routes._scan_with_clamd", return_value=None) as mock_scan:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                f"/attachments?case_id={uuid.uuid4()}",
                files={
                    "file": ("test.jpg", io.BytesIO(_JPEG_HEADER + b"\x00" * 100), "image/jpeg")
                },
            )
        mock_scan.assert_awaited_once()

    app.dependency_overrides.clear()
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_upload_infected_file_rejected(
    mock_settings: Settings, mock_session: AsyncSession
) -> None:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import get_settings

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_session] = override_session

    with patch("emf_form.routes._scan_with_clamd", return_value="Eicar-Test-Signature"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                f"/attachments?case_id={uuid.uuid4()}",
                files={
                    "file": ("test.jpg", io.BytesIO(_JPEG_HEADER + b"\x00" * 100), "image/jpeg")
                },
            )

    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert "Eicar-Test-Signature" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_clamd_unreachable_allows_upload(
    mock_settings: Settings, mock_session: AsyncSession
) -> None:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import get_settings

    mock_settings.attachment_dir = MagicMock()  # type: ignore[assignment]
    mock_settings.attachment_dir.__truediv__ = MagicMock(return_value=MagicMock())

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_session] = override_session

    # clamd unreachable → _scan_with_clamd returns None (degraded gracefully)
    with patch("emf_form.routes._scan_with_clamd", return_value=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                f"/attachments?case_id={uuid.uuid4()}",
                files={
                    "file": ("test.jpg", io.BytesIO(_JPEG_HEADER + b"\x00" * 100), "image/jpeg")
                },
            )

    app.dependency_overrides.clear()
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# URL safety checking (Google Safe Browsing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_url_submission_accepted(sb_client: AsyncClient) -> None:
    payload = make_valid_payload(media_links=["https://drive.google.com/file/d/abc123/view"])
    with patch("emf_form.routes._check_urls_safe_browsing", return_value=[]) as mock_check:
        response = await sb_client.post("/api/submit", json=payload)
        mock_check.assert_awaited_once()
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_unsafe_url_blocks_submission(sb_client: AsyncClient) -> None:
    bad_url = "https://malware.example.com/evil"
    payload = make_valid_payload(media_links=[bad_url])
    with patch("emf_form.routes._check_urls_safe_browsing", return_value=[bad_url]):
        response = await sb_client.post("/api/submit", json=payload)
    assert response.status_code == 400
    assert "verified as safe" in response.json()["detail"]


@pytest.mark.asyncio
async def test_safe_browsing_api_failure_allows_submission(sb_client: AsyncClient) -> None:
    payload = make_valid_payload(media_links=["https://example.com/evidence"])
    # API failure → returns [] → submission goes through
    with patch("emf_form.routes._check_urls_safe_browsing", return_value=[]):
        response = await sb_client.post("/api/submit", json=payload)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_empty_safe_browsing_key_skips_check(client: AsyncClient) -> None:
    # client uses mock_settings which has google_safe_browsing_key = ""
    payload = make_valid_payload(media_links=["https://example.com/evidence"])
    with patch("emf_form.routes._check_urls_safe_browsing") as mock_check:
        response = await client.post("/api/submit", json=payload)
        mock_check.assert_not_called()
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Health endpoint — ClamAV and Safe Browsing checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_clamd_ok(mock_session: AsyncSession, client: AsyncClient) -> None:
    execute_result = MagicMock()
    mock_session.execute = AsyncMock(return_value=execute_result)  # type: ignore[method-assign]
    with patch("emf_form.routes._clamd_ping", return_value=True):
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["checks"]["clamav"] == "ok"


@pytest.mark.asyncio
async def test_health_clamd_unavailable(mock_session: AsyncSession, client: AsyncClient) -> None:
    execute_result = MagicMock()
    mock_session.execute = AsyncMock(return_value=execute_result)  # type: ignore[method-assign]
    with patch("emf_form.routes._clamd_ping", return_value=False):
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["checks"]["clamav"] == "unavailable"
    assert data["status"] == "ok"  # clamd unavailable is not a health failure


@pytest.mark.asyncio
async def test_health_safe_browsing_not_configured(
    mock_session: AsyncSession,
) -> None:
    from emf_shared.db import get_session

    from emf_form.main import app
    from emf_form.settings import Settings, get_settings

    settings = MagicMock(spec=Settings)
    settings.google_safe_browsing_key = ""

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    app.dependency_overrides[get_settings] = lambda: cast(Settings, settings)
    app.dependency_overrides[get_session] = override_session

    execute_result = MagicMock()
    mock_session.execute = AsyncMock(return_value=execute_result)  # type: ignore[method-assign]

    with patch("emf_form.routes._clamd_ping", return_value=False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/health")

    app.dependency_overrides.clear()
    assert response.json()["checks"]["safe_browsing"] == "not_configured"
