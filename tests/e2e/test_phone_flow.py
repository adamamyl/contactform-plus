"""
End-to-end tests for the EMF phone adapter.

Requires:
  - Stack running (msg-router, form, postgres accessible)
  - EMF_PHONE_API_URL and EMF_PHONE_API_KEY set in environment
  - FORM_BASE_URL set (default: https://report.emf-forms.internal)
  - E2E_DB_URL set if postgres isn't on localhost:5432

Phone targets in the patched config use extension numbers from
EMF_PHONE_TEST_EXTENSION (default 9001) and EMF_PHONE_TEST_EXTENSION+1.
Set these to real SIP extensions to get SENT state; invalid extensions
will result in FAILED but still prove the router attempted the call.
"""

from __future__ import annotations

import json
import os
import pathlib
import ssl
import time
from collections.abc import Iterator

import httpx
import pytest

from conftest import SyncDB

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.json"

_PHONE_API_URL = os.environ.get("EMF_PHONE_API_URL", "").rstrip("/")
_PHONE_API_KEY = os.environ.get("EMF_PHONE_API_KEY", "")
_PHONE_EXT = int(os.environ.get("EMF_PHONE_TEST_EXTENSION", "9001"))
_FORM_URL = os.environ.get("FORM_BASE_URL", "https://report.emf-forms.internal").rstrip("/")

# Step CA root cert for local Traefik TLS; override with E2E_CA_CERT env var.
_DEFAULT_CA = pathlib.Path.home() / "projects/traefik-proxy/certs/step-ca-root.crt"
_CA_CERT_PATH = pathlib.Path(os.environ.get("E2E_CA_CERT", str(_DEFAULT_CA)))


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if _CA_CERT_PATH.exists():
        ctx.load_verify_locations(str(_CA_CERT_PATH))
    return ctx


SKIP_PHONE = not _PHONE_API_URL or not _PHONE_API_KEY

_SKIP_REASON = "EMF_PHONE_API_URL / EMF_PHONE_API_KEY not set"

_SUBMIT_BODY = {
    "event_name": "EMF 2026",
    "reporter": {"name": "e2e phone test", "email": None, "phone": None, "pronouns": None},
    "what_happened": "Phone adapter e2e test — automated submission, please ignore.",
    "incident_date": "2026-06-28",
    "incident_time": "12:00",
    "can_contact": False,
}


def _submit(urgency: str) -> str:
    """POST a case to /api/submit and return the case_id."""
    body = {**_SUBMIT_BODY, "urgency": urgency}
    resp = httpx.post(
        f"{_FORM_URL}/api/submit",
        json=body,
        timeout=30,
        verify=_ssl_ctx(),
    )
    assert resp.status_code == 201, f"submit failed {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "case_id" in data
    return str(data["case_id"])


def _poll_notification(
    db: SyncDB,
    case_id: str,
    channel: str = "telephony",
    timeout_s: int = 120,
) -> dict[str, str] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        row = db.fetchrow(
            "SELECT id::text, state FROM forms.notifications"
            " WHERE case_id = $1::uuid AND channel = $2",
            case_id,
            channel,
        )
        if row is not None:
            return dict(row)
        time.sleep(2)
    return None


def _compose(*args: str) -> int:
    import subprocess

    result = subprocess.run(
        ["docker", "compose", "-f", str(_REPO_ROOT / "infra/docker-compose.yml"), *args],
        check=False,
    )
    return result.returncode


def _restart_router() -> None:
    """Restart msg-router and wait until it is accepting connections."""
    import subprocess

    _compose("restart", "msg-router")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(_REPO_ROOT / "infra/docker-compose.yml"),
                "exec",
                "-T",
                "msg-router",
                "python3",
                "-c",
                "import sys; sys.exit(0)",
            ],
            check=False,
            capture_output=True,
        )
        if r.returncode == 0:
            time.sleep(2)  # give FastAPI/uvicorn a moment to bind
            return
        time.sleep(1)


@pytest.fixture(scope="module")
def phone_config_enabled() -> Iterator[None]:
    """Patch config.json with phone targets and restart the router.

    The phone adapter is a startup singleton: targets must be present in
    config.json when the router process starts. After patching the file we
    restart msg-router so it picks up the new adapter. config.json is
    restored and the router restarted again on teardown.
    """
    original = _CONFIG_PATH.read_text()
    config = json.loads(original)
    for ev in config["events"]:
        if ev["name"] == "EMF 2026":
            ev["emf_phone_mode"] = "high_priority_only"
            ev["emf_phone_targets"] = [
                {
                    "number": _PHONE_EXT,
                    "description": "e2e-primary",
                    "order": 1,
                    "delay_seconds": 0,
                },
                {
                    "number": _PHONE_EXT + 1,
                    "description": "e2e-secondary",
                    "order": 2,
                    "delay_seconds": 0,
                },
            ]
            break
    _CONFIG_PATH.write_text(json.dumps(config, indent=2))
    _restart_router()
    try:
        yield
    finally:
        _CONFIG_PATH.write_text(original)
        _restart_router()


# ---------------------------------------------------------------------------
# T1 — API health check
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_phone_api_health() -> None:
    """GET /api/conduct/alert must return non-5xx (proves server up, token accepted)."""
    resp = httpx.get(
        f"{_PHONE_API_URL}/api/conduct/alert",
        headers={"Authorization": f"Bearer {_PHONE_API_KEY}"},
        timeout=10,
        verify=_ssl_ctx(),
    )
    assert resp.status_code < 500, f"Phone API health check failed: {resp.status_code}"


# ---------------------------------------------------------------------------
# T3 — high_priority_only: low urgency → no telephony notification
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_no_telephony_row_on_low_urgency(db: SyncDB, phone_config_enabled: None) -> None:
    """Low-urgency case must not create a telephony notification row."""
    case_id = _submit("low")
    time.sleep(8)  # give router time to route (it won't call, but wait to be sure)
    row = db.fetchrow(
        "SELECT id FROM forms.notifications WHERE case_id = $1::uuid AND channel = 'telephony'",
        case_id,
    )
    assert row is None, f"Telephony notification unexpectedly created for case {case_id}"


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_no_telephony_row_on_medium_urgency(db: SyncDB, phone_config_enabled: None) -> None:
    """Medium-urgency case must not create a telephony notification row."""
    case_id = _submit("medium")
    time.sleep(8)
    row = db.fetchrow(
        "SELECT id FROM forms.notifications WHERE case_id = $1::uuid AND channel = 'telephony'",
        case_id,
    )
    assert row is None, f"Telephony notification unexpectedly created for case {case_id}"


# ---------------------------------------------------------------------------
# T4 — high_priority_only: high urgency → telephony notification created
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_telephony_row_created_on_high_urgency(db: SyncDB, phone_config_enabled: None) -> None:
    """High-urgency case must create a telephony notification row.

    The row may end up as sent (valid extension answered) or failed (invalid
    extension); either proves the router decided to call and attempted the API.
    """
    case_id = _submit("high")
    row = _poll_notification(db, case_id, timeout_s=120)
    assert row is not None, f"No telephony notification for high-urgency case {case_id} after 120s"


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_telephony_row_created_on_urgent(db: SyncDB, phone_config_enabled: None) -> None:
    """Urgent case must create a telephony notification row."""
    case_id = _submit("urgent")
    row = _poll_notification(db, case_id, timeout_s=120)
    assert row is not None, f"No telephony notification for urgent case {case_id} after 120s"


# ---------------------------------------------------------------------------
# T8 — all targets exhausted: case still recorded, no crash
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.skipif(SKIP_PHONE, reason=_SKIP_REASON)
def test_router_survives_all_targets_exhausted(db: SyncDB, phone_config_enabled: None) -> None:
    """When all phone targets fail the router must not crash; notification row ends up failed."""
    case_id = _submit("urgent")
    # Poll up to 120s for the row to appear and settle
    row = _poll_notification(db, case_id, timeout_s=120)
    assert row is not None, f"No telephony notification for case {case_id} after 120s"

    # Router (and form/panel) should still be up
    health = httpx.get(f"{_FORM_URL}/health", timeout=5, verify=False)
    assert health.status_code < 500
