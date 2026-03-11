from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from router.alert_router import AlertRouter
from router.channels.base import ChannelAdapter
from router.models import CaseAlert, NotifState
from router.settings import Settings


def _make_mock_adapter(
    available: bool = True, send_result: str | None = "msg-123"
) -> AsyncMock:
    adapter = AsyncMock(spec=ChannelAdapter)
    adapter.is_available.return_value = available
    adapter.send.return_value = send_result
    adapter.send_ack_confirmation.return_value = None
    return adapter


def _make_mock_session_factory(notif_mock: MagicMock | None = None) -> MagicMock:
    session = AsyncMock()
    if notif_mock is not None:
        session.get.return_value = notif_mock
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.return_value = cm
    return factory


def _make_router(
    email: AsyncMock,
    signal: AsyncMock | None = None,
    mattermost: AsyncMock | None = None,
    slack: AsyncMock | None = None,
    config: object = None,
    session_factory: MagicMock | None = None,
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
        session_factory=session_factory,
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
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    with patch("aiosmtplib.send") as mock_send:
        mock_send.return_value = None
        result = await adapter.send(alert)

    assert result is not None
    assert "@" in result


@pytest.mark.asyncio
async def test_email_adapter_send_ack_sets_in_reply_to() -> None:
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
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured: list[object] = []

    async def fake_send(msg: object, **kwargs: object) -> None:
        captured.append(msg)

    with patch("aiosmtplib.send", side_effect=fake_send):
        await adapter.send_ack_confirmation(alert, "alice", "<original@example.com>")

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
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
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
        config=cfg,
        email_adapter=email,
        signal_adapter=signal,
        mattermost_adapter=None,
        slack_adapter=None,
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
        config=cfg,
        email_adapter=email,
        signal_adapter=signal,
        mattermost_adapter=None,
        slack_adapter=None,
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
    sample_alert: CaseAlert,
) -> None:
    email = _make_mock_adapter(send_result="msg-ok")
    notif_mock = MagicMock()
    notif_mock.id = uuid.uuid4()
    router = _make_router(email, session_factory=_make_mock_session_factory(notif_mock))

    with patch("router.alert_router.Notification") as MockNotif:
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email)

    email.send.assert_called_once()
    assert notif_mock.state == NotifState.SENT
    assert notif_mock.message_id == "msg-ok"


@pytest.mark.asyncio
async def test_send_with_retry_fails_all_attempts(
    sample_alert: CaseAlert,
) -> None:
    email = _make_mock_adapter(send_result=None)
    notif_mock = MagicMock()
    router = _make_router(email, session_factory=_make_mock_session_factory(notif_mock))

    with (
        patch("router.alert_router.Notification") as MockNotif,
        patch("asyncio.sleep"),
    ):
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email)

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


# ---------------------------------------------------------------------------
# R.1 — Email ACK link in body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_ack_link_in_body() -> None:
    from router.channels.email import EmailAdapter

    adapter = EmailAdapter(
        host="localhost",
        port=587,
        from_addr="test@example.com",
        recipients=["to@example.com"],
        panel_url="http://localhost",
        ack_base_url="http://router:8002",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="high",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured: list[object] = []

    async def fake_send(msg: object, **kwargs: object) -> None:
        captured.append(msg)

    with patch("aiosmtplib.send", side_effect=fake_send):
        await adapter.send(alert, ack_token="test_token_abc")

    assert captured
    from email.message import EmailMessage
    msg = captured[0]
    assert isinstance(msg, EmailMessage)
    plain_part = msg.get_body(preferencelist=("plain",))
    assert plain_part is not None
    body = plain_part.get_content()
    assert "http://router:8002/ack/test_token_abc" in body


@pytest.mark.asyncio
async def test_email_no_ack_link_when_no_token() -> None:
    from router.channels.email import EmailAdapter

    adapter = EmailAdapter(
        host="localhost",
        port=587,
        from_addr="test@example.com",
        recipients=["to@example.com"],
        panel_url="http://localhost",
        ack_base_url="http://router:8002",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="high",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured: list[object] = []

    async def fake_send(msg: object, **kwargs: object) -> None:
        captured.append(msg)

    with patch("aiosmtplib.send", side_effect=fake_send):
        await adapter.send(alert)

    assert captured
    from email.message import EmailMessage
    msg = captured[0]
    assert isinstance(msg, EmailMessage)
    plain_part = msg.get_body(preferencelist=("plain",))
    assert plain_part is not None
    body = plain_part.get_content()
    assert "/ack/" not in body


# ---------------------------------------------------------------------------
# R.2 — mark_acked returns other SENT notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_acked_returns_other_sent_notifications(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    from router.models import Notification, NotifState

    email = _make_mock_adapter()
    router = _make_router(email)

    notif_id = uuid.uuid4()
    other_id = uuid.uuid4()

    notif = MagicMock(spec=Notification)
    notif.id = notif_id
    notif.case_id = uuid.UUID(sample_alert.case_id)
    notif.channel = "email"
    notif.state = NotifState.SENT
    notif.message_id = "msg-001"

    other_notif = MagicMock(spec=Notification)
    other_notif.id = other_id
    other_notif.channel = "signal"
    other_notif.state = NotifState.SENT
    other_notif.message_id = "ts-999"

    case_row = MagicMock()
    case_row.id = uuid.UUID(sample_alert.case_id)
    case_row.friendly_id = sample_alert.friendly_id
    case_row.event_name = sample_alert.event_name
    case_row.urgency = sample_alert.urgency
    case_row.status = sample_alert.status
    case_row.location_hint = sample_alert.location_hint
    case_row.location_lat = None
    case_row.location_lon = None
    case_row.created_at = sample_alert.created_at

    other_result = MagicMock()
    other_result.scalars.return_value.all.return_value = [other_notif]

    mock_session.get.side_effect = [notif, case_row]
    mock_session.execute.return_value = other_result

    alert_out, others = await router.mark_acked(notif_id, "alice", mock_session)
    assert alert_out is not None
    assert len(others) == 1
    assert others[0].channel == "signal"


@pytest.mark.asyncio
async def test_send_ack_to_all_channels_calls_each_adapter(
    mock_session: AsyncMock,
    sample_alert: CaseAlert,
) -> None:
    from router.models import Notification, NotifState

    email = _make_mock_adapter()
    signal = _make_mock_adapter()
    mattermost = _make_mock_adapter()

    router = _make_router(email, signal, mattermost)

    notif_email = MagicMock(spec=Notification)
    notif_email.channel = "email"
    notif_email.message_id = "mid-001"
    notif_email.state = NotifState.ACKED

    notif_signal = MagicMock(spec=Notification)
    notif_signal.channel = "signal"
    notif_signal.message_id = "ts-999"
    notif_signal.state = NotifState.SENT

    notifications = [notif_email, notif_signal]

    with patch("asyncio.create_task") as mock_task:
        await router.send_ack_to_all_channels(
            sample_alert, "alice", notifications, mock_session
        )

    assert mock_task.call_count == 2


# ---------------------------------------------------------------------------
# R.4 — _signal_phone_available delegates to phone adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_phone_available_delegates_to_phone_adapter() -> None:
    email = _make_mock_adapter()
    phone = _make_mock_adapter(available=True)

    router = _make_router(email)
    router._phone = phone  # type: ignore[attr-defined]

    result = await router._signal_phone_available()
    assert result is True
    phone.is_available.assert_called_once()


@pytest.mark.asyncio
async def test_signal_phone_available_returns_false_when_no_phone() -> None:
    email = _make_mock_adapter()
    router = _make_router(email)
    result = await router._signal_phone_available()
    assert result is False


# ---------------------------------------------------------------------------
# R.5 — Prometheus counter incremented
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_state_counter_incremented_on_success(
    sample_alert: CaseAlert,
) -> None:
    from unittest.mock import MagicMock

    counter = MagicMock()
    counter.labels.return_value = MagicMock()

    email = _make_mock_adapter(send_result="msg-ok")
    notif_mock = MagicMock()
    notif_mock.id = uuid.uuid4()
    router = _make_router(email, session_factory=_make_mock_session_factory(notif_mock))
    router._counter = counter  # type: ignore[attr-defined]

    with patch("router.alert_router.Notification") as MockNotif:
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email)

    counter.labels.assert_called_with(channel="email", state="sent")
    counter.labels.return_value.inc.assert_called()


@pytest.mark.asyncio
async def test_notification_state_counter_incremented_on_failure(
    sample_alert: CaseAlert,
) -> None:
    from unittest.mock import MagicMock

    counter = MagicMock()
    counter.labels.return_value = MagicMock()

    email = _make_mock_adapter(send_result=None)
    notif_mock = MagicMock()
    router = _make_router(email, session_factory=_make_mock_session_factory(notif_mock))
    router._counter = counter  # type: ignore[attr-defined]

    with (
        patch("router.alert_router.Notification") as MockNotif,
        patch("asyncio.sleep"),
    ):
        MockNotif.return_value = notif_mock
        await router._send_with_retry(sample_alert, "email", email)

    counter.labels.assert_called_with(channel="email", state="failed")


# ---------------------------------------------------------------------------
# U.1 — Signal map link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_message_includes_map_link() -> None:
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
        urgency="high",
        status="new",
        location_hint="Near stage",
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
        location_lat=52.0416,
        location_lon=-2.3770,
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
        await adapter.send(alert)

    assert captured
    message = str(captured[0]["message"])
    assert "map.emfcamp.org" in message
    assert "52.0416" in message
    assert "-2.377" in message


@pytest.mark.asyncio
async def test_signal_message_no_map_link_when_no_coords() -> None:
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
        urgency="medium",
        status="new",
        location_hint="Stage area",
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured: list[dict[str, object]] = []

    class FakeResp:
        status_code = 201

        def json(self) -> dict[str, object]:
            return {"timestamp": 1111}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, json: dict[str, object]) -> FakeResp:
            captured.append(json)
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await adapter.send(alert)

    assert captured
    assert "map.emfcamp.org" not in str(captured[0]["message"])


# ---------------------------------------------------------------------------
# U.2 — Signal ack confirmation includes acked_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_ack_confirmation_includes_acked_by() -> None:
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
        urgency="high",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured: list[dict[str, object]] = []

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, json: dict[str, object]) -> FakeResp:
            captured.append(json)
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await adapter.send_ack_confirmation(alert, "alice", "1234567890")

    assert captured
    assert "alice" in str(captured[0]["message"])


# ---------------------------------------------------------------------------
# U.3 / V — also_sent_via propagated to messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_also_sent_via() -> None:
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
        urgency="high",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
        also_sent_via=["email", "mattermost"],
    )

    captured: list[dict[str, object]] = []

    class FakeResp:
        status_code = 201

        def json(self) -> dict[str, object]:
            return {"timestamp": 9999}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, json: dict[str, object]) -> FakeResp:
            captured.append(json)
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await adapter.send(alert)

    assert captured
    message = str(captured[0]["message"])
    assert "email" in message
    assert "mattermost" in message


@pytest.mark.asyncio
async def test_email_also_sent_via_in_body() -> None:
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
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
        also_sent_via=["signal", "mattermost"],
    )

    captured: list[object] = []

    async def fake_send(msg: object, **kwargs: object) -> None:
        captured.append(msg)

    with patch("aiosmtplib.send", side_effect=fake_send):
        await adapter.send(alert)

    assert captured
    from email.message import EmailMessage
    msg = captured[0]
    assert isinstance(msg, EmailMessage)
    plain_part = msg.get_body(preferencelist=("plain",))
    assert plain_part is not None
    body = plain_part.get_content()
    assert "signal" in body and "mattermost" in body


# ---------------------------------------------------------------------------
# S — Mattermost Posts API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mattermost_posts_api_send() -> None:
    from router.channels.mattermost import MattermostAdapter

    adapter = MattermostAdapter(
        webhook_url=None,
        panel_url="http://panel",
        api_url="http://mattermost:8065",
        channel_id="chan123",
        token="mytoken",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="urgent",
        status="new",
        location_hint="Stage",
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured_requests: list[dict[str, object]] = []

    class FakeResp:
        status_code = 201

        def json(self) -> dict[str, object]:
            return {"id": "post_abc123"}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str]
        ) -> FakeResp:
            captured_requests.append({"url": url, "body": json})
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.send(alert)

    assert result == "post_abc123"
    assert captured_requests
    assert "api/v4/posts" in str(captured_requests[0]["url"])


@pytest.mark.asyncio
async def test_mattermost_ack_updates_post() -> None:
    from router.channels.mattermost import MattermostAdapter

    adapter = MattermostAdapter(
        webhook_url=None,
        panel_url="http://panel",
        api_url="http://mattermost:8065",
        channel_id="chan123",
        token="mytoken",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="urgent",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    captured_requests: list[dict[str, object]] = []

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(
            self, url: str, json: dict[str, object], headers: dict[str, str]
        ) -> FakeResp:
            captured_requests.append({"url": url, "body": json})
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await adapter.send_ack_confirmation(alert, "adam", "post_abc123")

    assert captured_requests
    url = str(captured_requests[0]["url"])
    assert "api/v4/posts" in url
    body = captured_requests[0]["body"]
    assert isinstance(body, dict)
    assert body.get("root_id") == "post_abc123"
    assert "adam" in str(body.get("message", ""))


@pytest.mark.asyncio
async def test_mattermost_falls_back_to_webhook() -> None:
    from router.channels.mattermost import MattermostAdapter

    adapter = MattermostAdapter(
        webhook_url="http://webhook/hook",
        panel_url="http://panel",
    )
    alert = CaseAlert(
        case_id=str(uuid.uuid4()),
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="low",
        status="new",
        location_hint=None,
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc
        ),
    )

    class FakeResp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {}

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        async def post(self, url: str, json: dict[str, object]) -> FakeResp:
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        result = await adapter.send(alert)

    assert result == "mattermost"


# ---------------------------------------------------------------------------
# T — Jambonz auto-call routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jambonz_auto_call_always_mode(
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
                jambonz_mode="always",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    email = _make_mock_adapter()
    phone = _make_mock_adapter(available=True)

    router = AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=None,
        mattermost_adapter=None,
        slack_adapter=None,
        phone_adapter=phone,
    )

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase

        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(sample_alert, mock_session)

    assert mock_task.call_count == 2  # email + telephony


@pytest.mark.asyncio
async def test_jambonz_auto_call_high_priority_only(
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
                jambonz_mode="high_priority_only",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    email = _make_mock_adapter()
    phone = _make_mock_adapter(available=True)

    router = AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=None,
        mattermost_adapter=None,
        slack_adapter=None,
        phone_adapter=phone,
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

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase

        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(low_alert, mock_session)

    assert mock_task.call_count == 1  # email only, no telephony for low urgency


@pytest.mark.asyncio
async def test_jambonz_disabled(
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
                jambonz_mode="disabled",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
    )

    email = _make_mock_adapter()
    phone = _make_mock_adapter(available=True)

    router = AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=None,
        mattermost_adapter=None,
        slack_adapter=None,
        phone_adapter=phone,
    )

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch("asyncio.create_task") as mock_task,
    ):
        from emf_shared.phase import Phase

        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(sample_alert, mock_session)

    assert mock_task.call_count == 1  # email only


# ---------------------------------------------------------------------------
# Mattermost action endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mattermost_webhook_action_endpoint_acks() -> None:
    from httpx import ASGITransport, AsyncClient
    from router.main import app, get_alert_router, get_session, get_settings
    from router.models import Notification

    notification_id = uuid.uuid4()
    case_id = str(uuid.uuid4())

    import datetime

    mock_alert = CaseAlert(
        case_id=case_id,
        friendly_id="a-b-c-d",
        event_name="emfcamp2026",
        urgency="urgent",
        status="new",
        location_hint=None,
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
    )

    mock_notif = MagicMock(spec=Notification)
    mock_notif.id = notification_id

    mock_router = AsyncMock(spec=AlertRouter)
    mock_router.mark_acked.return_value = (mock_alert, [])
    mock_router.send_ack_to_all_channels.return_value = None

    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = mock_notif

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_execute_result
    mock_settings = MagicMock(spec=Settings)
    mock_settings.mattermost_webhook_secret = ""

    app.dependency_overrides[get_alert_router] = lambda: mock_router
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: mock_settings

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/webhook/mattermost/action",
                json={
                    "user_name": "alice",
                    "context": {
                        "action": "ack",
                        "case_id": case_id,
                        "secret": "",
                    },
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Acknowledged" in resp.json()["update"]["message"]
    mock_router.mark_acked.assert_awaited_once()
    call_args = mock_router.mark_acked.call_args
    assert call_args.args[0] == notification_id
    assert call_args.args[1] == "alice"


@pytest.mark.asyncio
async def test_mattermost_webhook_action_endpoint_rejects_bad_secret() -> None:
    from httpx import ASGITransport, AsyncClient
    from router.main import app, get_alert_router, get_session, get_settings

    mock_router = AsyncMock(spec=AlertRouter)
    mock_session = AsyncMock()
    mock_settings = MagicMock(spec=Settings)
    mock_settings.mattermost_webhook_secret = "correct-secret"

    app.dependency_overrides[get_alert_router] = lambda: mock_router
    app.dependency_overrides[get_session] = lambda: mock_session
    app.dependency_overrides[get_settings] = lambda: mock_settings

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/webhook/mattermost/action",
                json={
                    "user_name": "alice",
                    "context": {
                        "action": "ack",
                        "notification_id": str(uuid.uuid4()),
                        "secret": "wrong-secret",
                    },
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    mock_router.mark_acked.assert_not_awaited()


# ---------------------------------------------------------------------------
# V.4 — also_sent_via integration: each channel sees the other two
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_also_sent_via_each_channel_sees_others(
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
                signal_mode="always",
            )
        ],
        conduct_emails=["team@emf.camp"],
        smtp=SmtpConfig(from_addr="conduct@emf.camp"),
        panel_base_url="http://localhost",
        mattermost_webhook="http://mattermost.local/hook",
    )

    email = _make_mock_adapter()
    signal = _make_mock_adapter()
    mattermost = _make_mock_adapter()

    router = AlertRouter(
        config=cfg,
        email_adapter=email,
        signal_adapter=signal,
        mattermost_adapter=mattermost,
        slack_adapter=None,
        session_factory=_make_mock_session_factory(),
    )

    sent_alerts: dict[str, CaseAlert] = {}

    async def capture_email(alert: CaseAlert, **kwargs: object) -> str:
        sent_alerts["email"] = alert
        return "msg-email"

    async def capture_signal(alert: CaseAlert, **kwargs: object) -> str:
        sent_alerts["signal"] = alert
        return "ts-signal"

    async def capture_mattermost(alert: CaseAlert, **kwargs: object) -> str:
        sent_alerts["mattermost"] = alert
        return "post-mm"

    email.send = capture_email  # type: ignore[method-assign]
    signal.send = capture_signal  # type: ignore[method-assign]
    mattermost.send = capture_mattermost  # type: ignore[method-assign]

    with (
        patch("router.alert_router.current_phase") as mock_phase,
        patch(
            "asyncio.create_task", side_effect=lambda c: asyncio.ensure_future(c)
        ) as _,
    ):
        from emf_shared.phase import Phase

        mock_phase.return_value = Phase.EVENT_TIME
        await router.route(sample_alert, mock_session)
        await asyncio.sleep(0)

    assert "email" in sent_alerts
    assert "signal" in sent_alerts
    assert "mattermost" in sent_alerts

    assert set(sent_alerts["email"].also_sent_via) == {"signal", "mattermost"}
    assert set(sent_alerts["signal"].also_sent_via) == {"email", "mattermost"}
    assert set(sent_alerts["mattermost"].also_sent_via) == {"email", "signal"}
