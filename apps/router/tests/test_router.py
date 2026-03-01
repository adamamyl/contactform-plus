from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from router.alert_router import AlertRouter
from router.channels.base import ChannelAdapter
from router.models import CaseAlert, NotifState
from router.settings import Settings


def _make_mock_adapter(available: bool = True, send_result: str | None = "msg-123") -> AsyncMock:
    adapter = AsyncMock(spec=ChannelAdapter)
    adapter.is_available.return_value = available
    adapter.send.return_value = send_result
    adapter.send_ack_confirmation.return_value = None
    return adapter


def _make_router(
    email: AsyncMock,
    signal: AsyncMock | None = None,
    mattermost: AsyncMock | None = None,
    slack: AsyncMock | None = None,
    config: object = None,
) -> AlertRouter:
    import json
    import pathlib
    from emf_shared.config import AppConfig

    cfg_path = pathlib.Path("tests/fixtures/config.json")
    cfg = AppConfig.model_validate(json.loads(cfg_path.read_text()))

    return AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=signal,
        mattermost_adapter=mattermost,
        slack_adapter=slack,
    )


# ---------------------------------------------------------------------------
# Email adapter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_adapter_is_available_false_when_unreachable() -> None:
    from router.channels.email import EmailAdapter

    adapter = EmailAdapter(
        host="localhost",
        port=9999,
        from_addr="test@example.com",
        recipients=["to@example.com"],
        panel_url="http://localhost",
        ack_base_url="http://localhost",
    )
    result = await adapter.is_available()
    assert result is False


@pytest.mark.asyncio
async def test_email_adapter_send_returns_message_id() -> None:
    from router.channels.email import EmailAdapter

    adapter = EmailAdapter(
        host="localhost",
        port=587,
        from_addr="test@example.com",
        recipients=["to@example.com"],
        panel_url="http://localhost",
        ack_base_url="http://localhost",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="high",
        status="new",
        location_hint="Stage",
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
    )

    with patch("aiosmtplib.send") as mock_send:
        mock_send.return_value = None
        result = await adapter.send(alert)

    assert result is not None
    assert "@" in result


@pytest.mark.asyncio
async def test_email_adapter_send_ack_sets_in_reply_to() -> None:
    from router.channels.email import EmailAdapter
    import aiosmtplib

    adapter = EmailAdapter(
        host="localhost",
        port=587,
        from_addr="test@example.com",
        recipients=["to@example.com"],
        panel_url="http://localhost",
        ack_base_url="http://localhost",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="high",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
    )

    captured: list[object] = []

    async def fake_send(msg: object, **kwargs: object) -> None:
        captured.append(msg)

    with patch("aiosmtplib.send", side_effect=fake_send):
        await adapter.send_ack_confirmation(alert, "<original@example.com>")

    assert captured
    sent_msg = captured[0]
    assert sent_msg["In-Reply-To"] == "<original@example.com>"  # type: ignore[index]
    assert sent_msg["References"] == "<original@example.com>"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Signal adapter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_adapter_send_posts_to_group() -> None:
    from router.channels.signal import SignalAdapter

    adapter = SignalAdapter(
        api_url="http://localhost:8080",
        sender="+441234567890",
        group_id="groupABC",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="urgent",
        status="new",
        location_hint="Stage",
        created_at=__import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc),
    )

    captured: list[dict[str, object]] = []

    class FakeResp:
        status_code = 201
        def json(self) -> dict[str, object]:
            return {"timestamp": 1234567890}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self
        async def __aexit__(self, *a: object) -> None:
            pass
        async def post(self, url: str, json: dict[str, object]) -> FakeResp:
            captured.append(json)
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.send(alert)

    assert result == "1234567890"
    assert captured
    assert "group.groupABC" in str(captured[0].get("recipients", []))


# ---------------------------------------------------------------------------
# AlertRouter routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_event_time_sends_email_and_signal(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    from datetime import date
    from emf_shared.config import AppConfig, EventConfig, SmtpConfig

    cfg = AppConfig(
        events=[
            EventConfig(
                name="emfcamp2026",
                start_date=date(2026, 5, 28),
                end_date=date(2026, 5, 31),
                signal_group_id="group1",
                signal_mode="always",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    email = _make_mock_adapter()
    signal = _make_mock_adapter()

    router = AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=signal,
        mattermost_adapter=None,
        slack_adapter=None,
    )

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase
        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(sample_alert, mock_session)

    assert mock_task.call_count == 2


@pytest.mark.asyncio
async def test_router_off_event_only_emails(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    email = _make_mock_adapter()
    signal = _make_mock_adapter()
    router = _make_router(email, signal)

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase
        mock_phase.return_value = Phase.PRE_EVENT
        await router.route(sample_alert, mock_session)

    assert mock_task.call_count == 1


@pytest.mark.asyncio
async def test_router_signal_mode_fallback_only_no_phone(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    from datetime import date
    from emf_shared.config import AppConfig, EventConfig, SmtpConfig

    cfg = AppConfig(
        events=[
            EventConfig(
                name="emfcamp2026",
                start_date=date(2026, 5, 28),
                end_date=date(2026, 5, 31),
                signal_group_id="group1",
                signal_mode="fallback_only",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    email = _make_mock_adapter()
    signal = _make_mock_adapter()
    router = AlertRouter(
        config=cfg, email_adapter=email, signal_adapter=signal,
        mattermost_adapter=None, slack_adapter=None,
    )

    # _signal_phone_available returns False by default → signal IS sent in fallback_only
    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase
        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(sample_alert, mock_session)

    assert mock_task.call_count == 2


@pytest.mark.asyncio
async def test_router_signal_mode_high_priority_only(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    from datetime import date
    from emf_shared.config import AppConfig, EventConfig, SmtpConfig

    cfg = AppConfig(
        events=[
            EventConfig(
                name="emfcamp2026",
                start_date=date(2026, 5, 28),
                end_date=date(2026, 5, 31),
                signal_group_id="group1",
                signal_mode="high_priority_and_fallback",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    low_alert = CaseAlert(
        case_id=sample_alert.case_id,
        friendly_id=sample_alert.friendly_id,
        event_name="emfcamp2026",
        urgency="low",
        status="new",
        location_hint=None,
        created_at=sample_alert.created_at,
    )

    email = _make_mock_adapter()
    signal = _make_mock_adapter()
    router = AlertRouter(
        config=cfg, email_adapter=email, signal_adapter=signal,
        mattermost_adapter=None, slack_adapter=None,
    )

    # low urgency, phone unavailable (False) → still sends signal because phone unavailable
    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase
        mock_phase.return_value = Phase.EVENT_TIME
        # patch phone available to True so signal is NOT sent for low urgency
        with patch.object(router, "_signal_phone_available", return_value=True):
            await router.route(low_alert, mock_session)

    # phone available + low urgency → only email
    assert mock_task.call_count == 1


# ---------------------------------------------------------------------------
# Notification state tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_with_retry_succeeds_first_attempt(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    email = _make_mock_adapter(send_result="msg-ok")
    router = _make_router(email)

    notif_mock = MagicMock()
    notif_mock.id = TEST_NOTIF_ID = uuid.uuid4()

    with patch("router.alert_router.Notification") as MockNotif:
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email, mock_session)

    email.send.assert_called_once()
    assert notif_mock.state == NotifState.SENT
    assert notif_mock.message_id == "msg-ok"


@pytest.mark.asyncio
async def test_send_with_retry_fails_all_attempts(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    email = _make_mock_adapter(send_result=None)
    router = _make_router(email)

    notif_mock = MagicMock()

    with (
        patch("router.alert_router.Notification") as MockNotif,
        patch("asyncio.sleep"),
    ):
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email, mock_session)

    assert email.send.call_count == 4  # 4 retry delays [0, 5, 10, 15]
    assert notif_mock.state == NotifState.FAILED


# ---------------------------------------------------------------------------
# ACK token tests
# ---------------------------------------------------------------------------


def test_ack_token_round_trip() -> None:
    from router.ack.tokens import create_ack_token, decode_ack_token

    notif_id = uuid.uuid4()
    secret = "super-secret-key-32-chars-padded!"
    token = create_ack_token(notif_id, secret)
    decoded = decode_ack_token(token, secret)
    assert decoded == notif_id
