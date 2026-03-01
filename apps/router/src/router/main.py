from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from emf_shared.db import get_session, init_db
from router.ack.tokens import create_ack_token, decode_ack_token
from router.alert_router import AlertRouter
from router.channels.email import EmailAdapter
from router.channels.mattermost import MattermostAdapter
from router.channels.signal import SignalAdapter
from router.channels.slack import SlackAdapter
from router.listener import listen_for_cases
from router.models import Notification, NotifState
from router.settings import Settings

log = logging.getLogger(__name__)

_router_instance: AlertRouter | None = None
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()  # type: ignore[call-arg]
    return _settings_instance


def get_alert_router() -> AlertRouter:
    if _router_instance is None:
        raise RuntimeError("AlertRouter not initialised")
    return _router_instance


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    global _router_instance
    settings = get_settings()
    init_db(settings.database_url)
    cfg = settings.app_config

    ev = cfg.events[0] if cfg.events else None
    signal_adapter: SignalAdapter | None = None
    if settings.signal_api_url and ev and ev.signal_group_id:
        signal_adapter = SignalAdapter(
            api_url=settings.signal_api_url,
            sender=settings.signal_sender,
            group_id=ev.signal_group_id,
        )

    mattermost_adapter: MattermostAdapter | None = None
    if cfg.mattermost_webhook:
        mattermost_adapter = MattermostAdapter(cfg.mattermost_webhook, cfg.panel_base_url)

    slack_adapter: SlackAdapter | None = None
    if cfg.slack_webhook:
        slack_adapter = SlackAdapter(cfg.slack_webhook, cfg.panel_base_url)

    recipients = []
    for event in cfg.events:
        recipients.extend(event.dispatcher_emails)
    if not recipients:
        recipients = cfg.conduct_emails

    email_adapter = EmailAdapter(
        host=cfg.smtp.host,
        port=cfg.smtp.port,
        from_addr=cfg.smtp.from_addr,
        recipients=recipients,
        panel_url=cfg.panel_base_url,
        ack_base_url=settings.ack_base_url or cfg.panel_base_url,
        password=settings.smtp_password,
        use_tls=cfg.smtp.use_tls,
        username=cfg.smtp.username,
    )

    _router_instance = AlertRouter(
        config=cfg,
        email_adapter=email_adapter,
        signal_adapter=signal_adapter,
        mattermost_adapter=mattermost_adapter,
        slack_adapter=slack_adapter,
    )

    task = asyncio.create_task(listen_for_cases(settings.database_url, _router_instance))
    yield
    task.cancel()


app = FastAPI(title="EMF Router Service", lifespan=lifespan)
api = APIRouter()


# ---------------------------------------------------------------------------
# Signal webhook — emoji reactions
# ---------------------------------------------------------------------------


class SignalEnvelope(BaseModel):
    source: str = ""
    dataMessage: dict[str, object] = {}


class SignalWebhookBody(BaseModel):
    envelope: SignalEnvelope = SignalEnvelope()


@api.post("/webhook/signal")
async def signal_webhook(
    body: SignalWebhookBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    alert_router: Annotated[AlertRouter, Depends(get_alert_router)],
) -> dict[str, bool]:
    data = body.envelope.dataMessage
    reaction = data.get("reaction", {})
    if not isinstance(reaction, dict):
        return {"ok": True}

    emoji = reaction.get("emoji", "")
    if emoji != "🤙":
        return {"ok": True}

    target_ts = str(reaction.get("targetSentTimestamp", ""))
    if not target_ts:
        return {"ok": True}

    result = await session.execute(
        select(Notification).where(Notification.message_id == target_ts)
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return {"ok": True}

    acked_by = str(body.envelope.source or "signal")
    alert = await alert_router.mark_acked(notif.id, acked_by, session)
    if alert and notif.message_id:
        await alert_router.send_ack_confirmations(alert, "signal", notif.message_id)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Email ACK magic link
# ---------------------------------------------------------------------------


@api.get("/ack/{token}", response_class=HTMLResponse)
async def email_ack(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    alert_router: Annotated[AlertRouter, Depends(get_alert_router)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    try:
        notification_id = decode_ack_token(token, settings.secret_key)
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    alert = await alert_router.mark_acked(notification_id, "email_link", session)
    if alert is None:
        return HTMLResponse(
            content="<p>This case is already acknowledged or the link has expired.</p>",
            status_code=200,
        )

    notif = await session.get(Notification, notification_id)
    if notif and notif.message_id:
        await alert_router.send_ack_confirmations(alert, "email", notif.message_id)

    html = (
        f"<h1>✅ Acknowledged</h1>"
        f"<p>Case <strong>{alert.friendly_id}</strong> has been marked as acknowledged.</p>"
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@api.get("/health")
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
    alert_router: Annotated[AlertRouter, Depends(get_alert_router)],
) -> dict[str, object]:
    from sqlalchemy import text

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    email_ok = await alert_router._email.is_available()
    signal_ok = (
        await alert_router._signal.is_available() if alert_router._signal else None
    )

    checks: dict[str, object] = {
        "database": "ok" if db_ok else "error",
        "email": "ok" if email_ok else "error",
    }
    if signal_ok is not None:
        checks["signal"] = "ok" if signal_ok else "error"

    overall = "ok" if db_ok else "degraded"
    return {"status": overall, "checks": checks, "version": "0.1.0"}


app.include_router(api)
