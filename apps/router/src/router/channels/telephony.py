from __future__ import annotations

import logging

import httpx

from router.channels.base import ChannelAdapter
from router.models import CaseAlert

log = logging.getLogger(__name__)


class TelephonyAdapter(ChannelAdapter):
    """Channel adapter that initiates outbound calls via the Jambonz API."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        account_sid: str,
        application_sid: str,
        tts_service_url: str,
        from_number: str,
        to_number: str | None = None,
        tts_audio_base_url: str = "",
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._account_sid = account_sid
        self._application_sid = application_sid
        self._tts_url = tts_service_url.rstrip("/")
        self._tts_audio_base_url = (tts_audio_base_url or tts_service_url).rstrip("/")
        self._from_number = from_number
        self._to_number = to_number or from_number

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def is_available(self) -> bool:
        if not self._api_url or not self._account_sid:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self._api_url}/v1/Accounts/{self._account_sid}",
                    headers=self._headers(),
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def _get_tts_url(self, alert: CaseAlert) -> str | None:
        payload = {
            "friendly_id": alert.friendly_id,
            "urgency": alert.urgency,
            "location_hint": alert.location_hint,
            "include_dtmf": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self._tts_url}/synthesise/file", json=payload)
            if resp.status_code == 200:
                rel_url: str = resp.json()["audio_url"]
                return f"{self._tts_audio_base_url}{rel_url}"
            log.warning("TTS /synthesise/file returned %s", resp.status_code)
            return None
        except Exception:
            log.exception("Failed to get TTS URL for case %s", alert.case_id)
            return None

    async def send(self, alert: CaseAlert) -> str | None:
        audio_url = await self._get_tts_url(alert)
        if audio_url is None:
            return None

        if self._to_number.startswith("+"):
            to_field: dict[str, str] = {"type": "phone", "number": self._to_number}
        elif self._to_number.startswith("sip:"):
            to_field = {"type": "sip", "sipUri": self._to_number}
        else:
            to_field = {"type": "user", "name": self._to_number}
        payload: dict[str, object] = {
            "application_sid": self._application_sid,
            "to": to_field,
            "from": self._from_number,
            "tag": {
                "case_id": alert.case_id,
                "audio_url": audio_url,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._api_url}/v1/Accounts/{self._account_sid}/Calls",
                    json=payload,
                    headers=self._headers(),
                )
            if resp.status_code == 201:
                call_sid: str = resp.json().get("sid", "")
                return call_sid or "telephony"
            log.warning(
                "Jambonz Calls API returned %s for case %s", resp.status_code, alert.case_id
            )
            return None
        except Exception:
            log.exception("TelephonyAdapter.send failed for case %s", alert.case_id)
            return None

    async def send_ack_confirmation(
        self, alert: CaseAlert, acked_by: str, message_id: str
    ) -> None:
        pass
