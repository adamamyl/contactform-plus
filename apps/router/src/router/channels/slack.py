from __future__ import annotations

import logging

import httpx

from emf_shared.tracing import outbound_headers
from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)

URGENCY_EMOJI: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "urgent": "🔴",
}


class SlackAdapter(ChannelAdapter):
    def __init__(self, webhook_url: str, panel_url: str) -> None:
        self._webhook_url = webhook_url
        self._panel_url = panel_url

    async def is_available(self) -> bool:
        return bool(self._webhook_url)

    async def send(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        text = (
            f"{emoji} *New {alert.urgency} case*: {alert.friendly_id}\n"
            f"Event: {alert.event_name} | Location: {alert.location_hint or 'not specified'}\n"
            f"<{self._panel_url}/cases/{alert.case_id}|View case>"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._webhook_url, json={"text": text}, headers=outbound_headers()
                )
                if resp.status_code == 200:
                    return "slack"
                log.warning(
                    "SlackAdapter.send got %s for case %s",
                    resp.status_code,
                    alert.case_id,
                )
                return None
        except Exception:
            log.exception("SlackAdapter.send failed for case %s", alert.case_id)
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        text = f"✅ Case {alert.friendly_id} acknowledged by {acked_by}."
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    self._webhook_url, json={"text": text}, headers=outbound_headers()
                )
        except Exception:
            log.exception(
                "SlackAdapter.send_ack_confirmation failed for case %s", alert.case_id
            )
