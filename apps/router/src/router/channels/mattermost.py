from __future__ import annotations

import logging

import httpx

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


class MattermostAdapter(ChannelAdapter):
    def __init__(
        self,
        webhook_url: str | None,
        panel_url: str,
        api_url: str | None = None,
        channel_id: str | None = None,
        token: str | None = None,
        action_url: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._panel_url = panel_url
        self._api_url = api_url.rstrip("/") if api_url else None
        self._channel_id = channel_id
        self._token = token
        self._action_url = action_url
        self._webhook_secret = webhook_secret

    def _uses_posts_api(self) -> bool:
        return bool(self._api_url and self._channel_id and self._token)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def is_available(self) -> bool:
        if self._uses_posts_api():
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self._api_url}/api/v4/system/ping")
                    return resp.status_code == 200
            except Exception:
                return False
        return bool(self._webhook_url)

    async def send(self, alert: CaseAlert) -> str | None:
        if self._uses_posts_api():
            return await self._send_posts_api(alert)
        return await self._send_webhook(alert)

    async def _send_posts_api(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        colour = URGENCY_COLOUR.get(alert.urgency, "#607d8b")
        fields: list[dict[str, object]] = [
            {"title": "Event", "value": alert.event_name, "short": True},
            {"title": "Location", "value": alert.location_hint or "not specified", "short": True},
        ]
        if alert.also_sent_via:
            fields.append(
                {"title": "Also sent via", "value": ", ".join(alert.also_sent_via), "short": True}
            )

        actions: list[dict[str, object]] = []
        if self._action_url:
            context: dict[str, object] = {"action": "ack", "case_id": alert.case_id}
            if self._webhook_secret:
                context["secret"] = self._webhook_secret
            actions = [
                {
                    "name": "Acknowledge",
                    "type": "button",
                    "integration": {
                        "url": self._action_url,
                        "context": context,
                    },
                }
            ]

        body: dict[str, object] = {
            "channel_id": self._channel_id,
            "message": f"{emoji} New {alert.urgency} case: {alert.friendly_id}",
            "props": {
                "attachments": [
                    {
                        "color": colour,
                        "title": f"{emoji} New {alert.urgency} case: {alert.friendly_id}",
                        "title_link": f"{self._panel_url}/cases/{alert.case_id}",
                        "fields": fields,
                        "actions": actions,
                    }
                ]
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._api_url}/api/v4/posts",
                    json=body,
                    headers=self._auth_headers(),
                )
            if resp.status_code == 201:
                post_id: str = resp.json().get("id", "")
                return post_id or None
            log.warning(
                "MattermostAdapter Posts API returned %s for case %s",
                resp.status_code,
                alert.case_id,
            )
        except Exception:
            log.exception("MattermostAdapter._send_posts_api failed for case %s", alert.case_id)

        if self._webhook_url:
            log.info("Falling back to webhook for case %s", alert.case_id)
            return await self._send_webhook(alert)
        return None

    async def _send_webhook(self, alert: CaseAlert) -> str | None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        text = (
            f"{emoji} **New {alert.urgency} case**: {alert.friendly_id}\n"
            f"Event: {alert.event_name} | Location: {alert.location_hint or 'not specified'}\n"
            f"[View case]({self._panel_url}/cases/{alert.case_id})"
        )
        if alert.also_sent_via:
            text += f"\nAlso sent via: {', '.join(alert.also_sent_via)}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._webhook_url, json={"text": text})  # type: ignore[arg-type]
                if resp.status_code == 200:
                    return "mattermost"
                log.warning(
                    "MattermostAdapter webhook got %s for case %s", resp.status_code, alert.case_id
                )
                return None
        except Exception:
            log.exception("MattermostAdapter._send_webhook failed for case %s", alert.case_id)
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        if self._uses_posts_api() and message_id not in ("mattermost", ""):
            await self._update_post_ack(alert, acked_by, message_id)
            return
        if self._webhook_url:
            await self._send_webhook_ack(alert, acked_by)

    async def _update_post_ack(
        self, alert: CaseAlert, acked_by: str, post_id: str
    ) -> None:
        emoji = URGENCY_EMOJI.get(alert.urgency, "⚪")
        channel_id = self._channel_id or ""
        if not channel_id:
            log.warning("MattermostAdapter: no channel_id configured, cannot post ACK reply")
            return

        body: dict[str, object] = {
            "channel_id": channel_id,
            "root_id": post_id,
            "message": f"✅ {emoji} Case {alert.friendly_id} acknowledged by {acked_by}",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._api_url}/api/v4/posts",
                    json=body,
                    headers=self._auth_headers(),
                )
            if resp.status_code not in (200, 201):
                log.warning(
                    "MattermostAdapter reply post returned %s for case %s",
                    resp.status_code,
                    alert.case_id,
                )
        except Exception:
            log.exception(
                "MattermostAdapter._update_post_ack failed for case %s", alert.case_id
            )

    async def _send_webhook_ack(self, alert: CaseAlert, acked_by: str) -> None:
        text = f"✅ Case {alert.friendly_id} acknowledged by {acked_by}."
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self._webhook_url, json={"text": text})  # type: ignore[arg-type]
        except Exception:
            log.exception(
                "MattermostAdapter._send_webhook_ack failed for case %s", alert.case_id
            )
