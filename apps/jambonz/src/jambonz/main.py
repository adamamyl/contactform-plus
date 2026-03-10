from __future__ import annotations

import logging
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from jambonz.adapter import JambonzAdapter

log = logging.getLogger(__name__)

ROUTER_INTERNAL_URL = os.environ.get("ROUTER_INTERNAL_URL", "http://msg-router:8002")
ROUTER_INTERNAL_SECRET = os.environ.get("ROUTER_INTERNAL_SECRET", "")
WEBHOOK_BASE_URL = os.environ.get("JAMBONZ_WEBHOOK_BASE_URL", "https://panel.emf-forms.internal")
TTS_INTERNAL_URL = os.environ.get("TTS_SERVICE_URL", "http://tts:8003")

# call_sid → {audio_url, case_id}
_call_registry: dict[str, dict[str, str]] = {}

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
api = APIRouter()
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


async def _call_router_ack(case_id: str, acked_by: str) -> None:
    headers: dict[str, str] = {}
    if ROUTER_INTERNAL_SECRET:
        headers["X-Internal-Secret"] = ROUTER_INTERNAL_SECRET
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{ROUTER_INTERNAL_URL}/internal/ack/{case_id}",
                json={"acked_by": acked_by},
                headers=headers,
            )
    except Exception:
        log.warning("Failed to notify router of DTMF ACK for case %s", case_id)


class RegisterBody(BaseModel):
    audio_url: str
    case_id: str


@api.post("/internal/register/{call_sid}")
async def register_call(call_sid: str, body: RegisterBody) -> dict[str, bool]:
    _call_registry[call_sid] = {"audio_url": body.audio_url, "case_id": body.case_id}
    return {"ok": True}


class JambonzWebhookBody(BaseModel):
    call_sid: str = ""
    call_status: str = ""
    digits: str = ""
    tag: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Calling webhook — Jambonz fires this once when the call needs instructions.
# Set as the application "Calling webhook" in the Jambonz console.
# ---------------------------------------------------------------------------


@api.post("/webhook/jambonz/call")
async def jambonz_call_webhook(
    body: JambonzWebhookBody,
    case_id: str = Query(default=""),
    audio_url: str = Query(default=""),
) -> object:
    import urllib.parse

    pressed = body.digits.strip()
    registered = _call_registry.get(body.call_sid, {})
    effective_case_id = case_id or registered.get("case_id", "")
    effective_audio_url = audio_url or registered.get("audio_url", "")
    print(f"CALL sid={body.call_sid!r} status={body.call_status!r} audio={effective_audio_url!r} case={effective_case_id!r}", flush=True)

    if pressed:
        if pressed.startswith("1") and effective_case_id:
            log.info("DTMF ACK: case %s acknowledged via call %s", effective_case_id, body.call_sid)
            await _call_router_ack(effective_case_id, "jambonz_dtmf")
        return {}

    if effective_audio_url and effective_case_id:
        params = urllib.parse.urlencode({"case_id": effective_case_id, "audio_url": effective_audio_url})
        action_url = f"{WEBHOOK_BASE_URL.rstrip('/')}/webhook/jambonz/call?{params}"
        return [
            {"verb": "play", "url": effective_audio_url},
            {"verb": "gather", "input": ["digits"], "numDigits": 1, "timeout": 30, "actionHook": action_url},
        ]

    return {}


# ---------------------------------------------------------------------------
# Status webhook — Jambonz fires this for every call state change.
# Set as the application "Call status webhook" in the Jambonz console.
# Must always return {} — returning verbs here causes call termination.
# ---------------------------------------------------------------------------


@api.post("/webhook/jambonz/status")
async def jambonz_status_webhook(body: JambonzWebhookBody) -> dict[str, object]:
    log.info("STATUS sid=%s status=%s", body.call_sid, body.call_status)
    return {}


# ---------------------------------------------------------------------------
# Audio proxy — lets Jambonz (cloud) fetch TTS files via this public adapter
# ---------------------------------------------------------------------------


@api.head("/audio/{filename}")
async def proxy_audio_head(filename: str) -> Response:
    return Response(status_code=200, media_type="audio/wav")


@api.get("/audio/{filename}")
async def proxy_audio(filename: str) -> Response:
    url = f"{TTS_INTERNAL_URL.rstrip('/')}/audio/{filename}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "audio/wav"),
        )
    except Exception:
        log.exception("Failed to proxy audio %s", filename)
        return Response(status_code=502)


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
