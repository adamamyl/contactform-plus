from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from prometheus_client import Counter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from emf_shared.config import AppConfig, EventConfig
from emf_shared.phase import Phase, current_phase
from router.channels.base import ChannelAdapter
from router.models import CaseAlert, CaseRouterView, Notification, NotifState

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

RETRY_DELAYS_MINUTES: list[int] = [0, 5, 10, 15]


class AlertRouter:
    def __init__(
        self,
        config: AppConfig,
        email_adapter: ChannelAdapter,
        signal_adapter: ChannelAdapter | None,
        mattermost_adapter: ChannelAdapter | None,
        slack_adapter: ChannelAdapter | None,
        phone_adapter: ChannelAdapter | None = None,
        secret_key: str = "",
        counter: Counter | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        local_dev: bool = False,
    ) -> None:
        self._config = config
        self._email = email_adapter
        self._signal = signal_adapter
        self._mattermost = mattermost_adapter
        self._slack = slack_adapter
        self._phone = phone_adapter
        self._secret_key = secret_key
        self._counter = counter
        self._session_factory = session_factory
        self._local_dev = local_dev

    def _event_config(self, event_name: str) -> EventConfig | None:
        for ev in self._config.events:
            if ev.name == event_name:
                return ev
        return None

    def _adapter_map(self) -> dict[str, ChannelAdapter | None]:
        return {
            "email": self._email,
            "signal": self._signal,
            "mattermost": self._mattermost,
            "slack": self._slack,
            "telephony": self._phone,
        }

    async def route(self, alert: CaseAlert, session: AsyncSession) -> None:
        phase = Phase.EVENT_TIME if self._local_dev else current_phase(self._config)
        ev = self._event_config(alert.event_name)

        if phase == Phase.EVENT_TIME:
            await self._route_event_time(alert, ev, session)
        else:
            await self._route_off_event(alert, session)

    async def _route_event_time(
        self,
        alert: CaseAlert,
        ev: EventConfig | None,
        session: AsyncSession,
    ) -> None:
        signal_mode = ev.signal_mode if ev else "fallback_only"
        phone_available = await self._signal_phone_available()

        channels: list[tuple[str, ChannelAdapter]] = [("email", self._email)]

        if self._signal is not None:
            send_signal = (
                signal_mode == "always"
                or (signal_mode == "fallback_only" and not phone_available)
                or (
                    signal_mode == "high_priority_and_fallback"
                    and (alert.urgency in ("high", "urgent") or not phone_available)
                )
            )
            if send_signal:
                channels.append(("signal", self._signal))

        if self._mattermost is not None:
            channels.append(("mattermost", self._mattermost))

        if self._slack is not None:
            channels.append(("slack", self._slack))

        if self._phone is not None and await self._phone.is_available():
            jambonz_mode = ev.jambonz_mode if ev else "disabled"
            if jambonz_mode == "always" or (
                jambonz_mode == "high_priority_only"
                and alert.urgency in ("high", "urgent")
            ):
                channels.append(("telephony", self._phone))

        channel_names = [name for name, _ in channels]
        for channel_name, adapter in channels:
            others = [n for n in channel_names if n != channel_name]
            per_channel_alert = replace(alert, also_sent_via=others)
            asyncio.create_task(
                self._send_with_retry(per_channel_alert, channel_name, adapter)
            )

    async def _route_off_event(self, alert: CaseAlert, session: AsyncSession) -> None:
        asyncio.create_task(
            self._send_with_retry(alert, "email", self._email)
        )

    async def _signal_phone_available(self) -> bool:
        if self._phone is None:
            return False
        return await self._phone.is_available()

    def _inc_counter(self, channel: str, state: str) -> None:
        if self._counter is not None:
            self._counter.labels(channel=channel, state=state).inc()

    async def _send_with_retry(
        self,
        alert: CaseAlert,
        channel_name: str,
        adapter: ChannelAdapter,
    ) -> None:
        from router.ack.tokens import create_ack_token

        if self._session_factory is None:
            log.error("No session factory — cannot persist notification for %s", channel_name)
            return

        notif_id = uuid.uuid4()
        ack_token: str | None = None
        if channel_name == "email" and self._secret_key:
            ack_token = create_ack_token(notif_id, self._secret_key)

        async with self._session_factory() as session:
            notif = Notification(
                id=notif_id,
                case_id=uuid.UUID(alert.case_id),
                channel=channel_name,
                state=NotifState.PENDING,
                attempt_count=0,
            )
            session.add(notif)
            await session.commit()

        for attempt_idx, delay_minutes in enumerate(RETRY_DELAYS_MINUTES):
            if delay_minutes > 0:
                await asyncio.sleep(delay_minutes * 60)

            async with self._session_factory() as session:
                row = await session.get(Notification, notif_id)
                if row is None:
                    log.error("Notification %s disappeared from DB", notif_id)
                    return
                row.attempt_count = attempt_idx + 1
                row.last_attempt_at = datetime.now(tz=UTC)
                await session.commit()

            if channel_name == "email" and ack_token:
                from router.channels.email import EmailAdapter
                if isinstance(adapter, EmailAdapter):
                    message_id = await adapter.send(alert, ack_token=ack_token)
                else:
                    message_id = await adapter.send(alert)
            else:
                message_id = await adapter.send(alert)

            if message_id is not None:
                async with self._session_factory() as session:
                    row = await session.get(Notification, notif_id)
                    if row is not None:
                        row.state = NotifState.SENT
                        row.message_id = message_id
                        await session.commit()
                self._inc_counter(channel_name, "sent")
                log.info(
                    "Sent case %s via %s (attempt %d)",
                    alert.case_id,
                    channel_name,
                    attempt_idx + 1,
                )
                return

            self._inc_counter(channel_name, "retrying")
            log.warning(
                "Send attempt %d/%d failed for case %s via %s",
                attempt_idx + 1,
                len(RETRY_DELAYS_MINUTES),
                alert.case_id,
                channel_name,
            )

        async with self._session_factory() as session:
            row = await session.get(Notification, notif_id)
            if row is not None:
                row.state = NotifState.FAILED
                await session.commit()
        self._inc_counter(channel_name, "failed")
        log.error(
            "🚨 All %d send attempts failed for case %s via %s",
            len(RETRY_DELAYS_MINUTES),
            alert.case_id,
            channel_name,
        )

    async def mark_acked(
        self,
        notification_id: uuid.UUID,
        acked_by: str,
        session: AsyncSession,
    ) -> tuple[CaseAlert | None, list[Notification]]:
        notif = await session.get(Notification, notification_id)
        if notif is None or notif.state == NotifState.ACKED:
            return None, []

        now = datetime.now(tz=UTC)
        await session.execute(
            update(Notification)
            .where(Notification.id == notification_id)
            .values(state=NotifState.ACKED, acked_at=now, acked_by=acked_by)
        )
        self._inc_counter(str(notif.channel), "acked")

        case_id = notif.case_id
        other_result = await session.execute(
            select(Notification).where(
                Notification.case_id == case_id,
                Notification.state == NotifState.SENT,
                Notification.id != notification_id,
            )
        )
        other_notifications = list(other_result.scalars().all())

        case_row = await session.get(CaseRouterView, uuid.UUID(str(case_id)))
        if case_row is None:
            await session.commit()
            return None, []

        alert = CaseAlert(
            case_id=str(case_row.id),
            friendly_id=case_row.friendly_id,
            event_name=case_row.event_name,
            urgency=case_row.urgency,
            status=case_row.status,
            location_hint=case_row.location_hint,
            location_lat=case_row.location_lat,
            location_lon=case_row.location_lon,
            created_at=case_row.created_at,
        )
        await session.commit()
        return alert, other_notifications

    async def send_ack_to_all_channels(
        self,
        alert: CaseAlert,
        acked_by: str,
        notifications: list[Notification],
        session: AsyncSession,
    ) -> None:
        adapters = self._adapter_map()
        for notif in notifications:
            if not notif.message_id:
                continue
            adapter = adapters.get(str(notif.channel))
            if adapter is None:
                continue
            asyncio.create_task(
                adapter.send_ack_confirmation(alert, acked_by, str(notif.message_id))
            )

    async def load_alert_from_db(
        self, case_id: str, session: AsyncSession
    ) -> CaseAlert | None:
        result = await session.execute(
            select(CaseRouterView).where(CaseRouterView.id == uuid.UUID(case_id))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return CaseAlert(
            case_id=str(row.id),
            friendly_id=row.friendly_id,
            event_name=row.event_name,
            urgency=row.urgency,
            status=row.status,
            location_hint=row.location_hint,
            location_lat=row.location_lat,
            location_lon=row.location_lon,
            created_at=row.created_at,
        )

    async def load_sent_notifications(
        self, case_id: str, session: AsyncSession
    ) -> list[Notification]:
        result = await session.execute(
            select(Notification).where(
                Notification.case_id == uuid.UUID(case_id),
                Notification.message_id.is_not(None),
            )
        )
        return list(result.scalars().all())
