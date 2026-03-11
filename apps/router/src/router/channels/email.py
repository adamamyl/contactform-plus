from __future__ import annotations

import logging
from email.message import EmailMessage
from email.utils import make_msgid

import aiosmtplib
import resend

from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)

URGENCY_EMOJI: dict[str, str] = {
    "low": "📋",
    "medium": "🔔",
    "high": "⚠️",
    "urgent": "🚨",
}

URGENCY_COLOUR: dict[str, str] = {
    "urgent": "#c62828",
    "high": "#e65100",
    "medium": "#1565c0",
    "low": "#558b2f",
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
        resend_api_key: str = "",
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
        # Port 465 uses SSL-on-connect (use_tls); port 587 uses STARTTLS (start_tls)
        self._start_tls = use_tls and port != 465
        self._ssl = use_tls and port == 465
        self._resend_api_key = resend_api_key
        if resend_api_key:
            resend.api_key = resend_api_key

    def _use_resend(self) -> bool:
        return bool(self._resend_api_key)

    async def is_available(self) -> bool:
        if self._use_resend():
            return True
        try:
            async with aiosmtplib.SMTP(
                hostname=self._host,
                port=self._port,
                use_tls=self._ssl,
                start_tls=self._start_tls,
                timeout=5,
            ):
                return True
        except Exception:
            return False

    async def _send_via_resend(
        self, subject: str, body: str, reply_to_mid: str | None = None
    ) -> str | None:
        mid = make_msgid(domain=self._domain)
        headers: dict[str, str] = {"Message-ID": mid}
        if reply_to_mid:
            headers["In-Reply-To"] = reply_to_mid
            headers["References"] = reply_to_mid
        params: resend.Emails.SendParams = {
            "from": self._from,
            "to": self._recipients,
            "subject": subject,
            "text": body,
            "headers": headers,
        }
        try:
            email = resend.Emails.send(params)
            return mid if email.get("id") else None
        except Exception:
            log.exception("Resend send failed")
            return None

    async def _send_via_smtp(self, msg: EmailMessage) -> bool:
        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                use_tls=self._ssl,
                start_tls=self._start_tls,
                username=self._username,
                password=self._password or None,
                timeout=10,
            )
            return True
        except Exception:
            log.exception("SMTP send failed")
            return False

    def _location_str(self, alert: CaseAlert) -> str:
        if alert.location_hint:
            return alert.location_hint
        if alert.location_lat is not None and alert.location_lon is not None:
            return f"{alert.location_lat:.5f}, {alert.location_lon:.5f} (map pin)"
        return "not specified"

    def _build_body(
        self, alert: CaseAlert, ack_token: str | None = None
    ) -> tuple[str, str]:
        emoji = URGENCY_EMOJI.get(alert.urgency, "🔔")
        colour = URGENCY_COLOUR.get(alert.urgency, "#607d8b")
        location = self._location_str(alert)
        panel_link = f"{self._panel_url}/cases/{alert.case_id}"
        also = ", ".join(alert.also_sent_via) if alert.also_sent_via else ""

        plain_parts = [
            f"{emoji} New {alert.urgency} case: {alert.friendly_id}",
            "",
            f"Event:    {alert.event_name}",
            f"Location: {location}",
        ]
        if also:
            plain_parts.append(f"Also via: {also}")
        if ack_token:
            plain_parts += ["", f"Acknowledge: {self._ack_base_url}/ack/{ack_token}"]
        plain_parts += ["", f"Details: {panel_link}"]
        plain = "\n".join(plain_parts)

        ack_button = ""
        if ack_token:
            ack_url = f"{self._ack_base_url}/ack/{ack_token}"
            ack_button = (
                f'<p><a href="{ack_url}" style="background:{colour};color:#fff;'
                f'padding:10px 20px;text-decoration:none;border-radius:4px;font-weight:bold;">'
                f"✅ Acknowledge</a></p>"
            )
        also_row = (
            f"<tr><td style='color:#666'>Also via</td><td>{also}</td></tr>"
            if also
            else ""
        )
        html = f"""<div style="font-family:sans-serif;max-width:600px">
  <div style="background:{colour};color:#fff;padding:12px 16px;border-radius:4px 4px 0 0">
    <strong>{emoji} New {alert.urgency} case: {alert.friendly_id}</strong>
  </div>
  <div style="border:1px solid #ddd;border-top:none;padding:16px;border-radius:0 0 4px 4px">
    <table style="border-collapse:collapse;width:100%">
      <tr><td style="color:#666;padding:4px 8px 4px 0;width:90px">Event</td><td>{alert.event_name}</td></tr>
      <tr><td style="color:#666;padding:4px 8px 4px 0">Location</td><td>{location}</td></tr>
      {also_row}
    </table>
    {ack_button}
    <p><a href="{panel_link}">View full details</a></p>
  </div>
</div>"""
        return plain, html

    async def send(self, alert: CaseAlert, ack_token: str | None = None) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "🔔")
        subject = f"{emoji} [{alert.urgency.upper()}] New case: {alert.friendly_id}"
        plain, html = self._build_body(alert, ack_token)

        if self._use_resend():
            mid = make_msgid(domain=self._domain)
            params: resend.Emails.SendParams = {
                "from": self._from,
                "to": self._recipients,
                "subject": subject,
                "text": plain,
                "html": html,
                "headers": {"Message-ID": mid},
            }
            try:
                email = resend.Emails.send(params)
                return mid if email.get("id") else None
            except Exception:
                log.exception("Resend send failed for case %s", alert.case_id)
                return None

        mid = make_msgid(domain=self._domain)
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = ", ".join(self._recipients)
        msg["Subject"] = subject
        msg["Message-ID"] = mid
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html")
        ok = await self._send_via_smtp(msg)
        return mid if ok else None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "🔔")
        subject = f"{emoji} [{alert.urgency.upper()}] New case: {alert.friendly_id}"
        panel_link = f"{self._panel_url}/cases/{alert.case_id}"
        plain = (
            f"✅ Case {alert.friendly_id} has been acknowledged by {acked_by}.\n\n"
            f"View case: {panel_link}\n"
        )
        html = (
            f'<div style="font-family:sans-serif;max-width:600px">'
            f'<div style="background:#2e7d32;color:#fff;padding:12px 16px;border-radius:4px 4px 0 0">'
            f"<strong>✅ Acknowledged</strong>"
            f"</div>"
            f'<div style="border:1px solid #ddd;border-top:none;padding:16px;border-radius:0 0 4px 4px">'
            f"<p><strong>{alert.friendly_id}</strong> has been acknowledged by <strong>{acked_by}</strong>.</p>"
            f'<p><a href="{panel_link}">View case in panel</a></p>'
            f"</div>"
            f"</div>"
        )

        if self._use_resend():
            params: resend.Emails.SendParams = {
                "from": self._from,
                "to": self._recipients,
                "subject": subject,
                "text": plain,
                "html": html,
            }
            try:
                resend.Emails.send(params)
            except Exception:
                log.exception(
                    "Resend ACK confirmation failed for case %s", alert.case_id
                )
            return

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = ", ".join(self._recipients)
        msg["Subject"] = subject
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id
        msg["Message-ID"] = make_msgid(domain=self._domain)
        msg.set_content(plain)
        msg.add_alternative(html, subtype="html")
        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                use_tls=self._ssl,
                start_tls=self._start_tls,
                username=self._username,
                password=self._password or None,
                timeout=10,
            )
        except Exception:
            log.exception(
                "EmailAdapter.send_ack_confirmation failed for case %s", alert.case_id
            )
