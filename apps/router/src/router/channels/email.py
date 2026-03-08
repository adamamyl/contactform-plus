from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import make_msgid

import aiosmtplib

from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)

URGENCY_EMOJI: dict[str, str] = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🟠",
    "urgent": "🔴",
}


class EmailAdapter(ChannelAdapter):
    def __init__(
        self,
        host: str,
        port: int,
        from_addr: str,
        recipients: list[str],
        panel_url: str,
        ack_base_url: str,
        password: str = "",
        use_tls: bool = True,
        username: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_addr
        self._recipients = recipients
        self._panel_url = panel_url
        self._ack_base_url = ack_base_url
        self._password = password
        self._use_tls = use_tls
        self._username = username
        self._domain = from_addr.split("@")[-1]

    async def is_available(self) -> bool:
        try:
            async with aiosmtplib.SMTP(
                hostname=self._host,
                port=self._port,
                use_tls=self._use_tls,
                timeout=5,
            ):
                return True
        except Exception:
            return False

    async def send(self, alert: CaseAlert, ack_token: str | None = None) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = ", ".join(self._recipients)
        msg["Subject"] = f"{emoji} [{alert.urgency.upper()}] New case: {alert.friendly_id}"
        mid = make_msgid(domain=self._domain)
        msg["Message-ID"] = mid

        ack_line = ""
        if ack_token:
            ack_line = f"\nAcknowledge: {self._ack_base_url}/ack/{ack_token}\n"

        also_line = ""
        if alert.also_sent_via:
            also_line = f"Also sent via: {', '.join(alert.also_sent_via)}\n"

        body = (
            f"Case: {alert.friendly_id}\n"
            f"Urgency: {alert.urgency}\n"
            f"Event: {alert.event_name}\n"
            f"Location: {alert.location_hint or 'not specified'}\n"
            f"{also_line}"
            f"{ack_line}"
            f"\nView full details: {self._panel_url}/cases/{alert.case_id}\n"
        )
        msg.set_content(body)

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                use_tls=self._use_tls,
                username=self._username,
                password=self._password or None,
                timeout=10,
            )
            return mid
        except Exception:
            log.exception("EmailAdapter.send failed for case %s", alert.case_id)
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = ", ".join(self._recipients)
        msg["Subject"] = f"✅ ACK: {alert.friendly_id}"
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id
        msg["Message-ID"] = make_msgid(domain=self._domain)
        body = f"{emoji} Case {alert.friendly_id} has been acknowledged by {acked_by}.\n"
        msg.set_content(body)
        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                use_tls=self._use_tls,
                username=self._username,
                password=self._password or None,
                timeout=10,
            )
        except Exception:
            log.exception("EmailAdapter.send_ack_confirmation failed for case %s", alert.case_id)
