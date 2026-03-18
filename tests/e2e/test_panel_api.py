from __future__ import annotations

import httpx
import pytest


@pytest.mark.e2e
def test_panel_health_returns_ok(panel_client: httpx.Client) -> None:
    resp = panel_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.e2e
def test_panel_unauthenticated_redirects_to_oidc(panel_client: httpx.Client) -> None:
    resp = panel_client.get("/cases")
    assert resp.status_code in (302, 307), f"Expected redirect to OIDC, got {resp.status_code}"
    location = resp.headers.get("location", "")
    assert location, "Redirect has no Location header"


@pytest.mark.e2e
def test_panel_dispatcher_unauthenticated_returns_401(panel_client: httpx.Client) -> None:
    import uuid

    fake_notif_id = str(uuid.uuid4())
    resp = panel_client.post(
        f"/api/v1/dispatcher/cases/{fake_notif_id}/ack",
        json={"acked_by": "e2e-test"},
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert resp.status_code in (401, 403, 422), f"Expected auth failure, got {resp.status_code}"
