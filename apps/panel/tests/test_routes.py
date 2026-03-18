from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from emf_panel.dispatcher import _revoked, revoke_token


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
    from emf_shared.db import get_session
    from httpx import ASGITransport, AsyncClient

    from emf_panel.auth import require_conduct_team
    from emf_panel.main import app
    from emf_panel.settings import get_settings

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


# ---------------------------------------------------------------------------
# API v1 — cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_list_cases_returns_paginated(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/api/v1/cases")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_api_list_cases_pagination_params(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/api/v1/cases?limit=10&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 10
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_api_get_case_has_links(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    case_id = uuid.uuid4()
    case = MagicMock()
    case.id = case_id
    case.friendly_id = "EMF-001"
    case.event_name = "EMF 2026"
    case.urgency = "medium"
    case.status = "new"
    case.assignee = None
    case.tags = []
    case.location_hint = None
    case.form_data = {}
    case.created_at = MagicMock(isoformat=lambda: "2026-01-01T00:00:00+00:00")
    case.updated_at = MagicMock(isoformat=lambda: "2026-01-01T00:00:00+00:00")
    mock_session.get.return_value = case

    resp = await authed_client.get(f"/api/v1/cases/{case_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "_links" in data
    assert data["_links"]["self"] == f"/api/v1/cases/{case_id}"
    assert "history" in data["_links"]
    assert "status" in data["_links"]


@pytest.mark.asyncio
async def test_api_get_case_not_found(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    mock_session.get.return_value = None
    resp = await authed_client.get(f"/api/v1/cases/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_lookup_by_friendly_id(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    case_id = uuid.uuid4()
    case = MagicMock()
    case.id = case_id
    case.friendly_id = "EMF-001"
    result = MagicMock()
    result.scalars.return_value.first.return_value = case
    mock_session.execute.return_value = result

    resp = await authed_client.get("/api/v1/cases/lookup?friendly_id=EMF-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(case_id)
    assert data["friendly_id"] == "EMF-001"


@pytest.mark.asyncio
async def test_api_lookup_by_uuid(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    case_id = uuid.uuid4()
    case = MagicMock()
    case.id = case_id
    case.friendly_id = "EMF-042"
    mock_session.get.return_value = case

    resp = await authed_client.get(f"/api/v1/cases/lookup?id={case_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(case_id)
    assert data["friendly_id"] == "EMF-042"


@pytest.mark.asyncio
async def test_api_lookup_no_params_returns_422(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/api/v1/cases/lookup")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_api_case_history(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    from emf_panel.models import CaseHistory as CH

    h = MagicMock(spec=CH)
    h.id = 1
    h.changed_by = "alice"
    h.field = "status"
    h.old_value = "new"
    h.new_value = "assigned"
    h.changed_at = MagicMock(isoformat=lambda: "2026-01-01T00:00:00+00:00")

    result = MagicMock()
    result.scalars.return_value.all.return_value = [h]
    mock_session.execute.return_value = result

    case_id = uuid.uuid4()
    resp = await authed_client.get(f"/api/v1/cases/{case_id}/history")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["field"] == "status"
    assert rows[0]["changed_by"] == "alice"


# ---------------------------------------------------------------------------
# API v1 — status transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_status_transition(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/v1/cases/{case_id}/status",
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
        f"/api/v1/cases/{case_id}/status",
        json={"status": "in_progress"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_closed_to_any_rejected(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    closed_case = MagicMock()
    closed_case.id = uuid.uuid4()
    closed_case.status = "closed"
    mock_session.get.return_value = closed_case

    case_id = str(uuid.uuid4())
    resp = await authed_client.patch(
        f"/api/v1/cases/{case_id}/status",
        json={"status": "new"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# API v1 — urgency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_urgency_update_creates_history_row(
    authed_client: AsyncClient, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    case = MagicMock()
    case.urgency = "low"
    mock_session.get.return_value = case

    resp = await authed_client.patch(
        f"/api/v1/cases/{case_id}/urgency",
        json={"urgency": "high"},
    )
    assert resp.status_code == 200
    assert resp.json()["urgency"] == "high"

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
        f"/api/v1/cases/{case_id}/urgency",
        json={"urgency": "catastrophic"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# API v1 — lookup lists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_autocomplete(authed_client: AsyncClient, mock_session: AsyncMock) -> None:
    result = MagicMock()
    result.fetchall.return_value = [("incident",), ("noise",)]
    mock_session.execute.return_value = result
    resp = await authed_client.get("/api/v1/tags")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# API v1 — dispatcher sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dispatcher_session(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/api/v1/dispatcher/sessions", json={"send_to": None})
    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert "expires_in_hours" in data


@pytest.mark.asyncio
async def test_revoke_dispatcher_session(authed_client: AsyncClient, valid_token: str) -> None:
    import jwt as pyjwt

    from emf_panel.settings import get_settings

    s = get_settings()
    payload = pyjwt.decode(valid_token, s.secret_key, algorithms=["HS256"])
    jti = str(payload["jti"])

    resp = await authed_client.delete(f"/api/v1/dispatcher/sessions/{jti}")
    assert resp.status_code == 204
    _revoked.discard(jti)


@pytest.mark.asyncio
async def test_expired_dispatcher_token_rejected(client: AsyncClient, expired_token: str) -> None:
    resp = await client.get(f"/dispatcher?token={expired_token}", follow_redirects=False)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_revoked_dispatcher_token_rejected(
    client: AsyncClient, valid_token: str, settings: object
) -> None:
    import jwt as pyjwt

    s = settings  # type: ignore[assignment]
    payload = pyjwt.decode(valid_token, s.secret_key, algorithms=["HS256"])  # type: ignore[attr-defined]
    jti = str(payload["jti"])
    revoke_token(jti)
    resp = await client.get(f"/dispatcher?token={valid_token}", follow_redirects=False)
    assert resp.status_code == 401
    _revoked.discard(jti)


# ---------------------------------------------------------------------------
# API v1 — dispatcher case actions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_ack(
    client: AsyncClient, valid_token: str, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    resp = await client.post(
        f"/api/v1/dispatcher/cases/{case_id}/ack?token={valid_token}",
        json={"acked_by": "dispatcher"},
        cookies={"device_id": "test-device-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_dispatcher_trigger_call(
    client: AsyncClient, valid_token: str, mock_session: AsyncMock
) -> None:
    case_id = str(uuid.uuid4())
    resp = await client.post(
        f"/api/v1/dispatcher/cases/{case_id}/calls?token={valid_token}",
        cookies={"device_id": "test-device-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
