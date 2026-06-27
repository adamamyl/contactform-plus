from __future__ import annotations

import asyncio
import logging

import httpx

from emf_shared.config import EMFPhoneTarget
from emf_shared.tracing import outbound_headers
from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)

_URGENCY_WORDS: dict[str, str] = {
    "low": "low priority",
    "medium": "medium priority",
    "high": "high priority",
    "urgent": "urgent",
}

_TERMINAL_RESULTS = {"ACKNOWLEDGE", "SKIP", "NO-ANSWER", "HANGUP", "NO-INPUT"}


def _build_message(alert: CaseAlert) -> str:
    urgency = _URGENCY_WORDS.get(alert.urgency, alert.urgency)
    spoken_id = alert.friendly_id.replace("-", " ")
    location = f" Location: {alert.location_hint}." if alert.location_hint else ""
    return f"New {urgency} conduct case. Case reference: {spoken_id}.{location}"


class EMFPhoneAdapter(ChannelAdapter):
    def __init__(
        self,
        api_url: str,
        api_key: str,
        targets: list[EMFPhoneTarget],
        router_self_url: str,
        router_internal_secret: str,
        timeout: float = 90.0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._targets = sorted(targets, key=lambda t: t.order)
        self._router_self_url = router_self_url.rstrip("/")
        self._router_internal_secret = router_internal_secret
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", **outbound_headers()}

    async def is_available(self) -> bool:
        if not self._api_url or not self._api_key or not self._targets:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self._api_url}/api/conduct/alert",
                    headers=self._headers(),
                )
            return resp.status_code < 500
        except Exception:
            return False

    async def send(self, alert: CaseAlert) -> str | None:
        message = _build_message(alert)

        for i, target in enumerate(self._targets):
            if i > 0 and target.delay_seconds > 0:
                log.info(
                    "EMF phone: waiting %ds before calling %s (%d) for case %s",
                    target.delay_seconds,
                    target.description,
                    target.number,
                    alert.case_id,
                )
                await asyncio.sleep(target.delay_seconds)

            log.info(
                "EMF phone: calling %s (%d) for case %s",
                target.description,
                target.number,
                alert.case_id,
            )
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._api_url}/api/conduct/alert",
                        json={"number": target.number, "message": message},
                        headers=self._headers(),
                    )
                if resp.status_code != 200:
                    log.warning(
                        "EMF phone API returned %d for %s (%d), case %s — trying next target",
                        resp.status_code,
                        target.description,
                        target.number,
                        alert.case_id,
                    )
                    continue

                result: str = resp.json().get("result", "")
                log.info(
                    "EMF phone: %s (%d) result=%s for case %s",
                    target.description,
                    target.number,
                    result,
                    alert.case_id,
                )

                if result == "ACKNOWLEDGE":
                    await self._trigger_ack(alert.case_id)
                    return f"ACKNOWLEDGE:{target.number}"

                if result in _TERMINAL_RESULTS:
                    continue

                log.warning(
                    "EMF phone: unexpected result %r from %s for case %s — treating as failure",
                    result,
                    target.description,
                    alert.case_id,
                )

            except Exception:
                log.exception(
                    "EMF phone: error calling %s (%d) for case %s",
                    target.description,
                    target.number,
                    alert.case_id,
                )

        log.warning("EMF phone: all targets exhausted for case %s", alert.case_id)
        return None

    async def _trigger_ack(self, case_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self._router_self_url}/internal/ack/{case_id}",
                    json={"acked_by": "emf_phone"},
                    headers={
                        "X-Internal-Secret": self._router_internal_secret,
                        **outbound_headers(),
                    },
                )
        except Exception:
            log.exception("EMF phone: failed to trigger ack for case %s", case_id)

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        pass
