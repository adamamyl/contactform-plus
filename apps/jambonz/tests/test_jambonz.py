from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from jambonz.adapter import CaseAlert, JambonzAdapter
from jambonz.main import app, get_adapter


def _make_alert() -> CaseAlert:
    return CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="alpha-beta-gamma-delta",
        urgency="urgent",
        location_hint="Stage",
    )


def _make_adapter(**kwargs: object) -> JambonzAdapter:
    defaults: dict[str, object] = {
        "api_url": "http://jambonz.local",
        "api_key": "test-key",
        "account_sid": "acc-1",
        "application_sid": "app-1",
        "tts_service_url": "http://tts.local",
        "from_number": "+441234567890",
    }
    defaults.update(kwargs)
    return JambonzAdapter(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# JambonzAdapter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_available_returns_true_on_200() -> None:
    adapter = _make_adapter()

    class FakeResp:
        status_code = 200

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def get(self, url: str, headers: dict[str, str]) -> FakeResp:
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.is_available()
    assert result is True


@pytest.mark.asyncio
async def test_is_available_returns_false_on_error() -> None:
    adapter = _make_adapter(api_url="http://nonexistent.invalid")
    result = await adapter.is_available()
    assert result is False


@pytest.mark.asyncio
async def test_send_calls_tts_then_jambonz() -> None:
    adapter = _make_adapter()
    alert = _make_alert()

    class FakeTtsResp:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"audio_url": "/audio/tok123"}

    class FakeCallResp:
        status_code = 201

        def json(self) -> dict[str, str]:
            return {"sid": "call-sid-abc"}

    call_count = {"tts": 0, "jambonz": 0}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(
            self, url: str, json: object, headers: dict[str, str] | None = None
        ) -> object:
            if "synthesise" in url:
                call_count["tts"] += 1
                return FakeTtsResp()
            call_count["jambonz"] += 1
            return FakeCallResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.call("+441111111111", alert)

    assert result == "call-sid-abc"
    assert call_count["tts"] == 1
    assert call_count["jambonz"] == 1


@pytest.mark.asyncio
async def test_send_returns_none_when_tts_fails() -> None:
    adapter = _make_adapter()
    alert = _make_alert()

    class FakeTtsResp:
        status_code = 500

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, json: object, **kwargs: object) -> FakeTtsResp:
            return FakeTtsResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.call("+441111111111", alert)

    assert result is None


# ---------------------------------------------------------------------------
# DTMF webhook tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_adapter() -> JambonzAdapter:
    return _make_adapter()


@pytest_asyncio.fixture
async def client(mock_adapter: JambonzAdapter) -> AsyncClient:
    app.dependency_overrides[get_adapter] = lambda: mock_adapter
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dtmf_digit_1_acks(client: AsyncClient) -> None:
    case_id = str(uuid.uuid4())
    resp = await client.post(
        "/webhook/jambonz",
        json={"call_sid": "c1", "digit": "1", "case_id": case_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "acked"
    assert data["case_id"] == case_id


@pytest.mark.asyncio
async def test_dtmf_digit_1_calls_router_ack(client: AsyncClient) -> None:
    case_id = str(uuid.uuid4())
    with patch("jambonz.main._call_router_ack", new_callable=AsyncMock) as mock_ack:
        resp = await client.post(
            "/webhook/jambonz",
            json={"call_sid": "c1", "digit": "1", "case_id": case_id},
        )
    assert resp.status_code == 200
    mock_ack.assert_awaited_once_with(case_id, "jambonz_dtmf")


@pytest.mark.asyncio
async def test_dtmf_digit_2_does_not_ack(client: AsyncClient) -> None:
    case_id = str(uuid.uuid4())
    with patch("jambonz.main._call_router_ack", new_callable=AsyncMock) as mock_ack:
        resp = await client.post(
            "/webhook/jambonz",
            json={"call_sid": "c1", "digit": "2", "case_id": case_id},
        )
    assert resp.status_code == 200
    mock_ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_dtmf_digit_2_passes_to_next(client: AsyncClient) -> None:
    case_id = str(uuid.uuid4())
    resp = await client.post(
        "/webhook/jambonz",
        json={"call_sid": "c1", "digit": "2", "case_id": case_id},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "next"


@pytest.mark.asyncio
async def test_dtmf_no_case_id(client: AsyncClient) -> None:
    resp = await client.post(
        "/webhook/jambonz",
        json={"call_sid": "c1", "digit": "1", "case_id": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# Escalation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalation_stops_on_ack() -> None:
    from jambonz.escalation import escalating_call

    adapter = _make_adapter()
    alert = _make_alert()
    phone_numbers = {
        "call_group": "+441234567890",
        "shift_leader": "+441234567891",
        "escalation_number": "+441234567892",
    }

    call_count = {"n": 0}

    async def fake_call(number: str, a: CaseAlert) -> str | None:
        call_count["n"] += 1
        return "call-sid"

    async def acked_after_first(case_id: str) -> bool:
        return call_count["n"] >= 1

    adapter.call = fake_call  # type: ignore[method-assign]

    with patch("asyncio.sleep"):
        await escalating_call(adapter, alert, phone_numbers, acked_after_first)

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_escalation_tries_all_on_no_ack() -> None:
    from jambonz.escalation import escalating_call

    adapter = _make_adapter()
    alert = _make_alert()
    phone_numbers = {
        "call_group": "+441234567890",
        "shift_leader": "+441234567891",
        "escalation_number": "+441234567892",
    }

    call_count = {"n": 0}

    async def fake_call(number: str, a: CaseAlert) -> str | None:
        call_count["n"] += 1
        return "call-sid"

    async def never_acked(case_id: str) -> bool:
        return False

    adapter.call = fake_call  # type: ignore[method-assign]

    with (
        patch("asyncio.sleep"),
        patch("jambonz.escalation.wait_for_ack", return_value=False),
    ):
        await escalating_call(adapter, alert, phone_numbers, never_acked)

    assert call_count["n"] == 3
