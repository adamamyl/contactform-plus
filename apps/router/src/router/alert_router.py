from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from emf_shared.config import AppConfig, EventConfig
from emf_shared.phase import Phase, current_phase
from router.channels.base import ChannelAdapter
from router.models import CaseAlert, CaseRouterView, Notification, NotifState

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Retry delays in minutes: attempt 0 = immediate, then 5, 10, 15 min later
RETRY_DELAYS_MINUTES: list[int] = [0, 5, 10, 15]


class AlertRouter:
    def __init__(
        self,
        config: AppConfig,
        email_adapter: ChannelAdapter,
        signal_adapter: ChannelAdapter | None,
        mattermost_adapter: ChannelAdapter | None,
        slack_adapter: ChannelAdapter | None,
    ) -> None:
        self._config = config
        self._email = email_adapter
        self._signal = signal_adapter
        self._mattermost = mattermost_adapter
        self._slack = slack_adapter

    def _event_config(self, event_name: str) -> EventConfig | None:
        for ev in self._config.events:
            if ev.name == event_name:
                return ev
        return None

    async def route(self, alert: CaseAlert, session: AsyncSession) -> None:
        phase = current_phase(self._config)
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

        for channel_name, adapter in channels:
            asyncio.create_task(
                self._send_with_retry(alert, channel_name, adapter, session)
            )

    async def _route_off_event(self, alert: CaseAlert, session: AsyncSession) -> None:
        asyncio.create_task(
            self._send_with_retry(alert, "email", self._email, session)
        )

    async def _signal_phone_available(self) -> bool:
        return False

    async def _send_with_retry(
        self,
        alert: CaseAlert,
        channel_name: str,
        adapter: ChannelAdapter,
        session: AsyncSession,
    ) -> None:
        notif_id = uuid.uuid4()
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

            notif.attempt_count = attempt_idx + 1
            notif.last_attempt_at = datetime.now(tz=UTC)
            await session.commit()

            message_id = await adapter.send(alert)

            if message_id is not None:
                notif.state = NotifState.SENT
                notif.message_id = message_id
                await session.commit()
                log.info(
                    "Sent case %s via %s (attempt %d)",
                    alert.case_id,
                    channel_name,
                    attempt_idx + 1,
                )
                return

            log.warning(
                "Send attempt %d/%d failed for case %s via %s",
                attempt_idx + 1,
                len(RETRY_DELAYS_MINUTES),
                alert.case_id,
                channel_name,
            )

        notif.state = NotifState.FAILED
        await session.commit()
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
    ) -> CaseAlert | None:
        """Mark a notification as acked and return the associated CaseAlert."""
        notif = await session.get(Notification, notification_id)
        if notif is None or notif.state == NotifState.ACKED:
            return None

        now = datetime.now(tz=UTC)
        await session.execute(
            update(Notification)
            .where(Notification.id == notification_id)
            .values(state=NotifState.ACKED, acked_at=now, acked_by=acked_by)
        )

        case_row = await session.get(CaseRouterView, uuid.UUID(str(notif.case_id)))
        if case_row is None:
            await session.commit()
            return None

        alert = CaseAlert(
            case_id=str(case_row.id),
            friendly_id=case_row.friendly_id,
            event_name=case_row.event_name,
            urgency=case_row.urgency,
            status=case_row.status,
            location_hint=case_row.location_hint,
            created_at=case_row.created_at,
        )
        await session.commit()
        return alert

    async def send_ack_confirmations(
        self,
        alert: CaseAlert,
        channel_name: str,
        message_id: str,
    ) -> None:
        """Fire ACK confirmation messages on the given channel."""
        adapter_map: dict[str, ChannelAdapter | None] = {
            "email": self._email,
            "signal": self._signal,
            "mattermost": self._mattermost,
            "slack": self._slack,
        }
        adapter = adapter_map.get(channel_name)
        if adapter is not None:
            await adapter.send_ack_confirmation(alert, message_id)

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
            created_at=row.created_at,
        )
