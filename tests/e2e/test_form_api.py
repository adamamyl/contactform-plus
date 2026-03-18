from __future__ import annotations

import uuid

import httpx
import pytest

from helpers import make_valid_payload


@pytest.mark.e2e
def test_health_returns_ok(form_client: httpx.Client) -> None:
    resp = form_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.e2e
def test_valid_submission_returns_201(form_client: httpx.Client) -> None:
    resp = form_client.post("/api/submit", json=make_valid_payload())
    assert resp.status_code == 201
    data = resp.json()
    assert "case_id" in data
    assert "friendly_id" in data


@pytest.mark.e2e
def test_honeypot_filled_returns_200_silent_drop(form_client: httpx.Client) -> None:
    resp = form_client.post("/api/submit", json=make_valid_payload(website="spam bot"))
    assert resp.status_code == 200
    assert resp.json()["friendly_id"] == "silent-drop"


@pytest.mark.e2e
def test_idempotency_deduplicates(form_client: httpx.Client) -> None:
    token = str(uuid.uuid4())
    payload = make_valid_payload()
    r1 = form_client.post("/api/submit", json=payload, headers={"X-Idempotency-Key": token})
    r2 = form_client.post("/api/submit", json=payload, headers={"X-Idempotency-Key": token})
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.json()["case_id"] == r2.json()["case_id"]


@pytest.mark.e2e
def test_what_happened_too_short_returns_422(form_client: httpx.Client) -> None:
    resp = form_client.post("/api/submit", json=make_valid_payload(what_happened="short"))
    assert resp.status_code == 422


@pytest.mark.e2e
def test_what_happened_too_long_returns_422(form_client: httpx.Client) -> None:
    resp = form_client.post("/api/submit", json=make_valid_payload(what_happened="x" * 10001))
    assert resp.status_code == 422


@pytest.mark.e2e
def test_invalid_urgency_returns_422(form_client: httpx.Client) -> None:
    resp = form_client.post("/api/submit", json=make_valid_payload(urgency="critical"))
    assert resp.status_code == 422


@pytest.mark.e2e
def test_xss_payload_stored_not_executed(form_client: httpx.Client) -> None:
    xss = "<script>alert('xss')</script> Something happened here at the event."
    resp = form_client.post("/api/submit", json=make_valid_payload(what_happened=xss))
    assert resp.status_code in (201, 422)
    assert "<script>" not in resp.text


@pytest.mark.e2e
def test_sql_injection_stored_safely(form_client: httpx.Client) -> None:
    sqli = "'; DROP TABLE cases; -- something happened at the event"
    resp = form_client.post("/api/submit", json=make_valid_payload(what_happened=sqli))
    assert resp.status_code in (201, 422)
    assert resp.status_code != 500


@pytest.mark.e2e
def test_rate_limit_triggers_429(form_client: httpx.Client) -> None:
    import time

    triggered = False
    for i in range(15):
        resp = form_client.post(
            "/api/submit",
            json=make_valid_payload(
                what_happened=f"Rate limit test submission {i} at the event during testing."
            ),
        )
        if resp.status_code == 429:
            triggered = True
            break
    assert triggered, "Rate limit (429) was never triggered after 15 rapid requests"
    # Wait for the rate-limit window to reset so subsequent browser tests aren't affected.
    time.sleep(11)
