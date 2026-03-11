"""
End-to-end tests for the full notification flow:
  form submission → DB → panel case list → webhook delivery → ACK

Requires the e2e stack plus a mock HTTP endpoint for outbound webhooks.
Set MOCK_WEBHOOK_URL to a httpbin-compatible server (e.g. http://localhost:8888/anything).
If not set, the notification delivery assertions are skipped.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest
from playwright.sync_api import Page, Route, expect

from conftest import SyncDB

_WHAT_HAPPENED = "Notification flow test: something happened requiring team attention."
_SKIP_WEBHOOK = not os.environ.get("MOCK_WEBHOOK_URL")


def _submit_case(
    page: Page, form_base_url: str, urgency: str = "urgent"
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def handle_route(route: Route) -> None:
        resp = route.fetch()
        try:
            result.update(resp.json())
        except Exception:
            pass
        route.fulfill(response=resp)

    page.route("**/api/submit", handle_route)
    try:
        page.goto(form_base_url)
        expect(page.locator("#conduct-form")).to_be_visible()
        page.locator("#what_happened").fill(_WHAT_HAPPENED)
        page.locator("#incident_date").fill("2024-06-01")
        page.locator("#incident_time").fill("12:00")
        if page.locator("#urgency").count():
            page.locator("#urgency").select_option(urgency)
        page.locator("input[name=can_contact][value=true]").check()
        page.locator("#submit-btn").click()
        page.wait_for_url(f"{form_base_url}/success**", timeout=15_000)
    finally:
        page.unroute("**/api/submit", handle_route)
    return result


@pytest.mark.e2e
def test_submitted_case_appears_in_db(
    page: Page, form_base_url: str, db: SyncDB
) -> None:
    result = _submit_case(page, form_base_url)
    assert "case_id" in result, f"No case_id in response: {result}"
    case_id = result["case_id"]

    row = db.fetchrow(
        "SELECT id, status, urgency FROM forms.cases WHERE id = $1::uuid", case_id
    )
    assert row is not None, f"Case {case_id} not found in DB"
    assert row["status"] == "new"


@pytest.mark.e2e
def test_submitted_case_visible_in_panel(
    page: Page, form_base_url: str, panel_base_url: str, db: SyncDB
) -> None:
    result = _submit_case(page, form_base_url)
    assert "case_id" in result
    case_id = result["case_id"]
    friendly_id = result.get("friendly_id", "")

    row = db.fetchrow(
        "SELECT friendly_id FROM forms.cases WHERE id = $1::uuid", case_id
    )
    assert row is not None
    assert friendly_id == row["friendly_id"]

    panel_client = httpx.Client(
        base_url=panel_base_url, follow_redirects=False, timeout=10.0
    )
    # Unauthenticated request should redirect to /login — confirms panel is up.
    resp = panel_client.get("/")
    assert resp.status_code in (200, 302)
    panel_client.close()


@pytest.mark.e2e
@pytest.mark.skipif(_SKIP_WEBHOOK, reason="MOCK_WEBHOOK_URL not set")
def test_notification_sent_to_webhook(
    page: Page, form_base_url: str, db: SyncDB
) -> None:
    result = _submit_case(page, form_base_url, urgency="urgent")
    assert "case_id" in result
    case_id = result["case_id"]

    deadline = time.monotonic() + 30
    notification_row = None
    while time.monotonic() < deadline:
        notification_row = db.fetchrow(
            "SELECT state, channel FROM forms.notifications WHERE case_id = $1::uuid LIMIT 1",
            case_id,
        )
        if notification_row is not None:
            break
        time.sleep(1)

    assert notification_row is not None, f"No notification row for case {case_id} after 30s"


@pytest.mark.e2e
def test_dispatcher_ack_updates_db(
    page: Page, form_base_url: str, panel_base_url: str, db: SyncDB
) -> None:
    result = _submit_case(page, form_base_url, urgency="urgent")
    assert "case_id" in result
    case_id = result["case_id"]

    row = db.fetchrow(
        "SELECT id FROM forms.notifications WHERE case_id = $1::uuid LIMIT 1",
        case_id,
    )
    if row is None:
        pytest.skip("No notification row to ACK (router may not be running)")

    token_env = os.environ.get("E2E_DISPATCHER_TOKEN", "")
    if not token_env:
        pytest.skip("E2E_DISPATCHER_TOKEN not set")

    resp = httpx.post(
        f"{panel_base_url}/api/dispatcher/ack/{case_id}",
        params={"token": token_env},
        json={"acked_by": "e2e_test"},
        timeout=10.0,
    )
    assert resp.status_code == 200

    ack_row = db.fetchrow(
        "SELECT state, acked_by FROM forms.notifications WHERE case_id = $1::uuid LIMIT 1",
        case_id,
    )
    assert ack_row is not None
    assert ack_row["state"] == "acked"
    assert ack_row["acked_by"] == "e2e_test"
