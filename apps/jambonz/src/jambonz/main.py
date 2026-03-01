from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel

from jambonz.adapter import CaseAlert, JambonzAdapter

log = logging.getLogger(__name__)

_adapter_instance: JambonzAdapter | None = None


def _make_adapter() -> JambonzAdapter:
    return JambonzAdapter(
        api_url=os.environ.get("JAMBONZ_API_URL", ""),
        api_key=os.environ.get("JAMBONZ_API_KEY", ""),
        account_sid=os.environ.get("JAMBONZ_ACCOUNT_SID", ""),
        application_sid=os.environ.get("JAMBONZ_APPLICATION_SID", ""),
        tts_service_url=os.environ.get("TTS_SERVICE_URL", "http://tts:8003"),
        from_number=os.environ.get("JAMBONZ_FROM_NUMBER", ""),
    )


def get_adapter() -> JambonzAdapter:
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = _make_adapter()
    return _adapter_instance


app = FastAPI(title="EMF Jambonz Adapter")
api = APIRouter()


# ---------------------------------------------------------------------------
# DTMF webhook — called by Jambonz when a digit is pressed
# ---------------------------------------------------------------------------


class DtmfWebhookBody(BaseModel):
    call_sid: str = ""
    digit: str = ""
    case_id: str = ""


@api.post("/webhook/jambonz")
async def jambonz_dtmf_webhook(body: DtmfWebhookBody) -> dict[str, object]:
    digit = body.digit.strip()
    case_id = body.case_id.strip()

    if not case_id:
        return {"ok": False, "reason": "no case_id"}

    if digit == "1":
        log.info("DTMF ACK: case %s acknowledged via call %s", case_id, body.call_sid)
        return {"ok": True, "action": "acked", "case_id": case_id}

    if digit == "2":
        log.info(
            "DTMF skip: case %s passed to next responder via call %s", case_id, body.call_sid
        )
        return {"ok": True, "action": "next", "case_id": case_id}

    return {"ok": True, "action": "ignored", "digit": digit}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@api.get("/health")
async def health(adapter: Annotated[JambonzAdapter, Depends(get_adapter)]) -> dict[str, object]:
    available = await adapter.is_available()
    return {
        "status": "ok" if available else "degraded",
        "checks": {"jambonz_api": "ok" if available else "error"},
        "version": "0.1.0",
    }


app.include_router(api)
