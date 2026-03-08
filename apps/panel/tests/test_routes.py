from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from emf_panel.dispatcher import _revoked, create_dispatcher_token, revoke_token


@pytest.mark.asyncio
async def test_unauthenticated_redirects_to_login(client: AsyncClient) -> None:
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert "/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_non_conduct_user_gets_403(
    mock_session: AsyncMock,
    settings: object,
    regular_user: dict[str, object],
) -> None:
    from emf_panel.main import app
    from emf_shared.db import get_session
    from emf_panel.settings import get_settings
    from emf_panel.auth import require_conduct_team
    from httpx import ASGITransport, AsyncClient

    async def bad_auth() -> dict[str, object]:
        from fastapi import HTTPException
        raise HTTPException(status_code=403)

    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[require_conduct_team] = bad_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/")
    app.dependency_overrides.clear()
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_conduct_user_can_list_cases(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/")
    assert resp.status_code == 200
    assert b"Cases" in resp.content


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_valid_status_transition(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/cases/{case_id}/status",
        json={"status": "assigned"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "assigned"


@pytest.mark.asyncio
async def test_invalid_transition_new_to_in_progress(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/cases/{case_id}/status",
        json={"status": "in_progress"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_closed_to_any_rejected(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    closed_case = MagicMock()
    closed_case.id = uuid.uuid4()
    closed_case.status = "closed"
    mock_session.get.return_value = closed_case

    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/cases/{case_id}/status",
        json={"status": "new"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_tag_autocomplete(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    result = MagicMock()
    result.fetchall.return_value = [("incident",), ("noise",)]
    mock_session.execute.return_value = result
    resp = await authed_client.get("/api/tags")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_dispatcher_session(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/api/dispatcher-session", json={"send_to": None})
    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert "expires_in_hours" in data


@pytest.mark.asyncio
async def test_expired_dispatcher_token_rejected(
    client: AsyncClient, expired_token: str
) -> None:
    resp = await client.get(f"/dispatcher?token={expired_token}", follow_redirects=False)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_revoked_dispatcher_token_rejected(
    client: AsyncClient, valid_token: str, settings: object
) -> None:
    from jose import jwt as jose_jwt
    from emf_panel.settings import Settings
    s = settings  # type: ignore[assignment]
    payload = jose_jwt.decode(valid_token, s.secret_key, algorithms=["HS256"])  # type: ignore[attr-defined]
    jti = str(payload["jti"])
    revoke_token(jti)
    resp = await client.get(f"/dispatcher?token={valid_token}", follow_redirects=False)
    assert resp.status_code == 401
    _revoked.discard(jti)


@pytest.mark.asyncio
async def test_dispatcher_ack(
    client: AsyncClient, valid_token: str, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    resp = await client.post(
        f"/api/dispatcher/ack/{case_id}?token={valid_token}",
        json={"acked_by": "dispatcher"},
        cookies={"device_id": "test-device-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_urgency_update_creates_history_row(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    case = MagicMock()
    case.urgency = "low"
    mock_session.get.return_value = case

    resp = await authed_client.patch(
        f"/api/cases/{case_id}/urgency",
        json={"urgency": "high"},
    )
    assert resp.status_code == 200
    assert resp.json()["urgency"] == "high"

    # session.add is called once for the CaseHistory row
    mock_session.add.assert_called_once()
    history_row = mock_session.add.call_args.args[0]
    from emf_panel.models import CaseHistory
    assert isinstance(history_row, CaseHistory)
    assert history_row.field == "urgency"
    assert history_row.old_value == "low"
    assert history_row.new_value == "high"


@pytest.mark.asyncio
async def test_urgency_update_rejects_invalid_level(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/cases/{case_id}/urgency",
        json={"urgency": "catastrophic"},
    )
    assert resp.status_code == 422
