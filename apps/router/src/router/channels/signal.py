from __future__ import annotations

import logging

import httpx

from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)

URGENCY_EMOJI: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "urgent": "🔴",
}


class SignalAdapter(ChannelAdapter):
    def __init__(self, api_url: str, sender: str, group_id: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._sender = sender
        self._group_id = group_id

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._api_url}/v1/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def send(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        text = (
            f"{emoji} *New {alert.urgency} case*: {alert.friendly_id}\n"
            f"Event: {alert.event_name}\n"
            f"Location: {alert.location_hint or 'not specified'}"
        )
        payload: dict[str, object] = {
            "message": text,
            "number": self._sender,
            "recipients": [f"group.{self._group_id}"],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self._api_url}/v2/send", json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    ts = str(data.get("timestamp", ""))
                    return ts or "sent"
                log.warning(
                    "SignalAdapter.send got %s for case %s", resp.status_code, alert.case_id
                )
                return None
        except Exception:
            log.exception("SignalAdapter.send failed for case %s", alert.case_id)
            return None

    async def send_ack_confirmation(self, alert: CaseAlert, message_id: str) -> None:
        text = f"✅ Case {alert.friendly_id} has been acknowledged."
        payload: dict[str, object] = {
            "message": text,
            "number": self._sender,
            "recipients": [f"group.{self._group_id}"],
            "quote_timestamp": int(message_id) if message_id.isdigit() else None,
            "quote_author": self._sender,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(f"{self._api_url}/v2/send", json=payload)
        except Exception:
            log.exception(
                "SignalAdapter.send_ack_confirmation failed for case %s", alert.case_id
            )
