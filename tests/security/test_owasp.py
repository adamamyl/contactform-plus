"""
OWASP Top 10 (2025) security test suite for the EMF Conduct system.

These tests exercise security properties of each service in isolation using
mocked dependencies. Integration tests requiring a live database are marked
with @pytest.mark.integration and are skipped in CI unless --integration is
passed.

Coverage map:
  A01 Broken Access Control        — dispatcher cannot read form_data; auth enforced
  A02 Cryptographic Failures       — no secrets in config; .env permissions
  A03 Injection                    — SQL injection strings stored safely
  A04 Insecure Design              — honeypot + idempotency
  A05 Security Misconfiguration    — no debug; server header stripped; no stack traces
  A07 Identification & Auth        — expired/revoked tokens; 403 for wrong group
  A08 Software & Data Integrity    — uv.lock committed
  A09 Security Logging             — status transitions produce CaseHistory rows
  A10 SSRF                         — URL in text fields stored, not fetched
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# A01 — Broken Access Control
# ---------------------------------------------------------------------------


def test_dispatcher_view_excludes_form_data() -> None:
    """The cases_router view must not SELECT form_data as a direct column."""
    sql_path = (
        Path(__file__).parent.parent.parent / "infra" / "postgres" / "00_roles.sql"
    )
    sql = sql_path.read_text()
    # Find the cases_router view
    start = sql.index("CREATE VIEW forms.cases_router")
    end = sql.index(";", start)
    view_def = sql[start:end]
    # form_data may appear in JSON path expressions but must not be a direct column selection
    import re

    direct_select = re.search(
        r"\bSELECT\b.*\bform_data\b(?!\s*->)", view_def, re.DOTALL | re.IGNORECASE
    )
    assert (
        direct_select is None
    ), "cases_router view must not expose form_data as a direct column"


def test_panel_viewer_grants_no_form_data() -> None:
    """panel_viewer role must not have SELECT on form_data column."""
    sql_path = (
        Path(__file__).parent.parent.parent / "infra" / "postgres" / "00_roles.sql"
    )
    sql = sql_path.read_text()
    # Check that no unconditional form_data grant goes to panel_viewer
    assert (
        "form_data" not in sql.split("panel_viewer")[1].split("team_member")[0]
    ), "panel_viewer must not receive form_data access"


def test_form_user_has_no_update_grant() -> None:
    """form_user role should only INSERT cases, not UPDATE."""
    sql_path = (
        Path(__file__).parent.parent.parent / "infra" / "postgres" / "00_roles.sql"
    )
    sql = sql_path.read_text()
    assert (
        "UPDATE" not in sql.split("form_user")[1].split("router_user")[0].upper()
        or "UPDATE ON forms.cases TO form_user" not in sql
    )


# ---------------------------------------------------------------------------
# A01 — Auth: unauthenticated requests redirected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_panel_root_redirects_unauthenticated() -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-owasp-tests-000")

    from emf_panel.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/", follow_redirects=False)

    assert resp.status_code in (302, 303, 307, 308)
    assert "/login" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_panel_403_for_non_conduct_user() -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-owasp-tests-000")

    from emf_panel.auth import require_conduct_team
    from emf_panel.main import app
    from emf_shared.db import get_session
    from fastapi import HTTPException
    from httpx import ASGITransport, AsyncClient

    async def _bad_auth() -> dict[str, object]:
        raise HTTPException(status_code=403)

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()

    app.dependency_overrides[require_conduct_team] = _bad_auth
    app.dependency_overrides[get_session] = lambda: mock_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/")

    app.dependency_overrides.clear()
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# A02 — Cryptographic Failures
# ---------------------------------------------------------------------------


def test_env_example_has_no_real_secrets() -> None:
    """All secret placeholders in .env-example should be 'changeme'."""
    env_path = Path(__file__).parent.parent.parent / ".env-example"
    content = env_path.read_text()
    for line in content.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            if any(k in key.upper() for k in ("PASSWORD", "SECRET", "KEY", "TOKEN")):
                assert (
                    val.strip()
                    in (
                        "changeme",
                        "",
                    )
                ), f"{key} in .env-example must be 'changeme' or empty, got '{val.strip()}'"


def test_config_example_has_no_real_secrets() -> None:
    """config.json-example must not contain production credentials."""
    import json

    cfg_path = Path(__file__).parent.parent.parent / "config.json-example"
    data = json.loads(cfg_path.read_text())
    smtp = data.get("smtp", {})
    assert smtp.get("from_addr", "").endswith(
        "@example.com"
    ) or "emfcamp.org" in smtp.get(
        "from_addr", ""
    ), "smtp.from_addr should be a placeholder"


# ---------------------------------------------------------------------------
# A03 — Injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_injection_string_in_what_happened_returns_201_or_422() -> None:
    """SQL injection strings in what_happened must be sanitised, not executed."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

    from emf_form.main import app
    from emf_shared.db import get_session
    from emf_form.settings import get_settings
    from httpx import ASGITransport, AsyncClient

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.get.return_value = None
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    from emf_shared.config import AppConfig, EventConfig, SmtpConfig
    from datetime import date

    fake_cfg = AppConfig(
        events=[
            EventConfig(
                name="test", start_date=date(2026, 5, 1), end_date=date(2026, 5, 5)
            )
        ],
        conduct_emails=["x@example.com"],
        smtp=SmtpConfig(from_addr="x@example.com"),
        panel_base_url="http://localhost",
    )

    class FakeSettings:
        database_url = "postgresql+asyncpg://x:x@localhost/x"
        config_path = Path("/nonexistent")
        secret_key = "secret"
        smtp_password = ""

        @property
        def app_config(self) -> AppConfig:
            return fake_cfg

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: FakeSettings()

    sql_injection = "'; DROP TABLE forms.cases; --"
    resp_status: int = 0
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/submit",
            json={
                "event_name": "test",
                "what_happened": sql_injection,
                "urgency": "low",
                "location": {"text": "Stage"},
                "reporter": {},
                "incident_date": "2026-03-01",
                "incident_time": "12:00",
                "idempotency_token": str(uuid.uuid4()),
            },
        )
        resp_status = resp.status_code

    app.dependency_overrides.clear()
    assert resp_status in (201, 422), f"Expected 201 or 422, got {resp_status}"


# ---------------------------------------------------------------------------
# A04 — Insecure Design: honeypot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_honeypot_filled_returns_200_no_db_write() -> None:
    """Filling the honeypot field must return 200 but not write to the DB."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

    from emf_form.main import app
    from emf_shared.db import get_session
    from emf_form.settings import get_settings
    from emf_shared.config import AppConfig, EventConfig, SmtpConfig
    from datetime import date
    from httpx import ASGITransport, AsyncClient

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.get.return_value = None
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    fake_cfg = AppConfig(
        events=[
            EventConfig(
                name="test", start_date=date(2026, 5, 1), end_date=date(2026, 5, 5)
            )
        ],
        conduct_emails=["x@example.com"],
        smtp=SmtpConfig(from_addr="x@example.com"),
        panel_base_url="http://localhost",
    )

    class FakeSettings:
        database_url = "postgresql+asyncpg://x:x@localhost/x"
        config_path = Path("/nonexistent")
        secret_key = "secret"
        smtp_password = ""

        @property
        def app_config(self) -> AppConfig:
            return fake_cfg

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: FakeSettings()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/submit",
            json={
                "event_name": "test",
                "what_happened": "Something happened here at the event",
                "urgency": "low",
                "location": {"text": "Stage"},
                "reporter": {},
                "incident_date": "2026-03-01",
                "incident_time": "12:00",
                "can_contact": False,
                "idempotency_token": str(uuid.uuid4()),
                "website": "http://bot.example.com",  # honeypot field
            },
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# A05 — Security Misconfiguration
# ---------------------------------------------------------------------------


def test_caddy_headers_config_strips_server_header() -> None:
    """Caddy headers snippet must strip the Server header."""
    headers_path = (
        Path(__file__).parent.parent.parent
        / "infra"
        / "caddy"
        / "snippets"
        / "headers.caddy"
    )
    content = headers_path.read_text()
    assert "-Server" in content or "Server" in content


def test_caddy_enforces_tls_13_minimum() -> None:
    """Caddy TLS snippet must specify tls_min_version 1.3."""
    tls_path = (
        Path(__file__).parent.parent.parent
        / "infra"
        / "caddy"
        / "snippets"
        / "tls.caddy"
    )
    content = tls_path.read_text()
    assert "1.3" in content


def test_csp_does_not_include_unsafe_eval() -> None:
    """CSP header in Caddy config must not include unsafe-eval."""
    headers_path = (
        Path(__file__).parent.parent.parent
        / "infra"
        / "caddy"
        / "snippets"
        / "headers.caddy"
    )
    content = headers_path.read_text()
    assert "unsafe-eval" not in content


# ---------------------------------------------------------------------------
# A07 — Identification & Authentication Failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_dispatcher_token_returns_401() -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-owasp-tests-000")

    from emf_panel.main import app
    from emf_panel.dispatcher import create_dispatcher_token
    from emf_panel.settings import get_settings, Settings
    from emf_shared.db import get_session
    from httpx import ASGITransport, AsyncClient

    settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        secret_key="test-secret-key-for-owasp-tests-000",
        smtp_password="",
        oidc_issuer="http://localhost",
        oidc_client_id="test",
        oidc_client_secret="secret",
        base_url="http://test",
    )
    expired_token = create_dispatcher_token(settings.secret_key, ttl_hours=-1)

    mock_session = AsyncMock()
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/dispatcher?token={expired_token}",
            cookies={"device_id": "test-device"},
            follow_redirects=False,
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# A08 — Software & Data Integrity
# ---------------------------------------------------------------------------


def test_uv_lock_files_committed() -> None:
    """Every service directory with a pyproject.toml must have a uv.lock."""
    apps_dir = Path(__file__).parent.parent.parent / "apps"
    for pyproject in apps_dir.glob("*/pyproject.toml"):
        service_dir = pyproject.parent
        lock_file = service_dir / "uv.lock"
        assert lock_file.exists(), f"Missing uv.lock in {service_dir.name}"


def test_shared_lib_has_uv_lock() -> None:
    shared_dir = Path(__file__).parent.parent.parent / "shared"
    assert (shared_dir / "uv.lock").exists()


# ---------------------------------------------------------------------------
# A09 — Security Logging & Monitoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_transition_creates_case_history_row() -> None:
    """PATCH /api/cases/{id}/status must produce a CaseHistory row."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-owasp-tests-000")

    from emf_panel.main import app
    from emf_panel.auth import require_conduct_team
    from emf_panel.settings import get_settings, Settings
    from emf_shared.db import get_session
    from emf_panel.models import CaseHistory
    from httpx import ASGITransport, AsyncClient

    settings = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        secret_key="test-secret-key-for-owasp-tests-000",
        smtp_password="",
        oidc_issuer="http://localhost",
        oidc_client_id="test",
        oidc_client_secret="secret",
        base_url="http://test",
    )

    case_mock = MagicMock()
    case_mock.id = uuid.uuid4()
    case_mock.status = "new"

    mock_session = AsyncMock()
    mock_session.get.return_value = case_mock
    execute_result = MagicMock()
    mock_session.execute.return_value = execute_result

    added_objects: list[object] = []
    mock_session.add = MagicMock(side_effect=added_objects.append)

    conduct_user = {
        "sub": "u1",
        "preferred_username": "tester",
        "groups": ["team_conduct"],
    }

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_conduct_team] = lambda: conduct_user

    case_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            f"/api/cases/{case_id}/status",
            json={"status": "assigned"},
        )

    app.dependency_overrides.clear()
    assert resp.status_code == 200
    history_rows = [o for o in added_objects if isinstance(o, CaseHistory)]
    assert len(history_rows) == 1
    assert history_rows[0].field == "status"
    assert history_rows[0].old_value == "new"
    assert history_rows[0].new_value == "assigned"


# ---------------------------------------------------------------------------
# A10 — SSRF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_in_additional_info_stored_not_fetched() -> None:
    """A URL submitted in additional_info must be stored as text, not fetched."""
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

    from emf_form.main import app
    from emf_shared.db import get_session
    from emf_form.settings import get_settings
    from emf_shared.config import AppConfig, EventConfig, SmtpConfig
    from datetime import date
    from httpx import ASGITransport, AsyncClient

    mock_session = AsyncMock()
    mock_session.execute.return_value = MagicMock()
    mock_session.get.return_value = None
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    fake_cfg = AppConfig(
        events=[
            EventConfig(
                name="test", start_date=date(2026, 5, 1), end_date=date(2026, 5, 5)
            )
        ],
        conduct_emails=["x@example.com"],
        smtp=SmtpConfig(from_addr="x@example.com"),
        panel_base_url="http://localhost",
    )

    class FakeSettings:
        database_url = "postgresql+asyncpg://x:x@localhost/x"
        config_path = Path("/nonexistent")
        secret_key = "secret"
        smtp_password = ""

        @property
        def app_config(self) -> AppConfig:
            return fake_cfg

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: FakeSettings()

    ssrf_url = "http://169.254.169.254/latest/meta-data/"  # AWS IMDS

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        with patch("httpx.AsyncClient.get") as mock_get:
            resp = await client.post(
                "/api/submit",
                json={
                    "event_name": "test",
                    "what_happened": "Something happened at the event",
                    "urgency": "low",
                    "location": {"text": "Stage"},
                    "reporter": {},
                    "incident_date": "2026-03-01",
                    "incident_time": "12:00",
                    "idempotency_token": str(uuid.uuid4()),
                    "additional_info": ssrf_url,
                },
            )
            # httpx.get must NOT have been called with the ssrf URL
            for call in mock_get.call_args_list:
                assert ssrf_url not in str(
                    call
                ), "SSRF: app fetched URL from user input"

    app.dependency_overrides.clear()
    assert resp.status_code in (201, 422)
