from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.responses import HTMLResponse
import jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from emf_shared.db import get_session, get_session_factory, init_db
from router.ack.tokens import decode_ack_token
from router.alert_router import AlertRouter
from router.channels.email import EmailAdapter
from router.channels.mattermost import MattermostAdapter
from router.channels.signal import SignalAdapter
from router.channels.slack import SlackAdapter
from router.channels.telephony import TelephonyAdapter
from router.listener import listen_for_cases
from router.models import Notification
from router.settings import Settings

log = logging.getLogger(__name__)

notification_dispatch_seconds = Histogram(
    "emf_notification_dispatch_seconds",
    "Time to dispatch a notification",
    ["channel"],
)
notification_state_total = Counter(
    "emf_notification_state_total",
    "Notification state transitions",
    ["channel", "state"],
)

_router_instance: AlertRouter | None = None
_settings_instance: Settings | None = None

SIGNAL_POLL_INTERVAL = 10  # seconds


async def _poll_signal_reactions(
    api_url: str,
    sender: str,
    alert_router: AlertRouter,
    session_factory: object,
) -> None:
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession as _AS

    factory: async_sessionmaker[_AS] = session_factory  # type: ignore[assignment]
    url = f"{api_url.rstrip('/')}/v1/receive/{sender}"
    while True:
        await asyncio.sleep(SIGNAL_POLL_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                continue
            messages = resp.json()
        except Exception:
            log.debug("Signal poll failed", exc_info=True)
            continue

        for msg in messages:
            env = msg.get("envelope", {})
            data_msg = env.get("dataMessage", {})
            reaction = data_msg.get("reaction")
            if not isinstance(reaction, dict):
                continue
            if reaction.get("emoji") != "🤙":
                continue
            target_ts = str(reaction.get("targetSentTimestamp", ""))
            if not target_ts:
                continue

            try:
                async with factory() as session:
                    result = await session.execute(
                        select(Notification).where(Notification.message_id == target_ts)
                    )
                    notif = result.scalar_one_or_none()
                    if notif is None:
                        continue
                    acked_by = str(env.get("source") or "signal")
                    alert, others = await alert_router.mark_acked(
                        notif.id, acked_by, session
                    )
                    if alert:
                        await alert_router.send_ack_to_all_channels(
                            alert, acked_by, others, session
                        )
                        log.info(
                            "Signal reaction ACK processed for case %s",
                            alert.friendly_id,
                        )
            except Exception:
                log.exception("Error processing Signal reaction")


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
    router_base_url = settings.ack_base_url or cfg.panel_base_url

    signal_adapter: SignalAdapter | None = None
    if settings.signal_api_url and ev and ev.signal_group_id:
        if cfg.site_map:
            map_base_url = cfg.site_map.map_url.rstrip("/")
        elif cfg.domains and cfg.domains.map:
            map_base_url = f"https://{cfg.domains.map}"
        else:
            map_base_url = "https://map.emfcamp.org"
        signal_adapter = SignalAdapter(
            api_url=settings.signal_api_url,
            sender=settings.signal_sender,
            group_id=ev.signal_group_id,
            panel_base_url=router_base_url,
            map_base_url=map_base_url,
        )
    mattermost_action_url = (
        f"{settings.router_self_url}/webhook/mattermost/action"
        if cfg.mattermost_url and settings.mattermost_token
        else None
    )
    mattermost_adapter: MattermostAdapter | None = None
    if cfg.mattermost_webhook or (
        cfg.mattermost_url and cfg.mattermost_channel_id and settings.mattermost_token
    ):
        mattermost_adapter = MattermostAdapter(
            webhook_url=cfg.mattermost_webhook,
            panel_url=cfg.panel_base_url,
            api_url=cfg.mattermost_url,
            channel_id=cfg.mattermost_channel_id,
            token=settings.mattermost_token or None,
            action_url=mattermost_action_url,
            webhook_secret=settings.mattermost_webhook_secret or None,
        )

    slack_adapter: SlackAdapter | None = None
    if cfg.slack_webhook:
        slack_adapter = SlackAdapter(cfg.slack_webhook, cfg.panel_base_url)

    jambonz_vars = [
        settings.jambonz_api_url,
        settings.jambonz_api_key,
        settings.jambonz_account_sid,
        settings.jambonz_application_sid,
        settings.jambonz_from_number,
    ]
    phone_adapter: TelephonyAdapter | None = None
    if all(jambonz_vars):
        phone_adapter = TelephonyAdapter(
            api_url=settings.jambonz_api_url,
            api_key=settings.jambonz_api_key,
            account_sid=settings.jambonz_account_sid,
            application_sid=settings.jambonz_application_sid,
            tts_service_url=settings.tts_service_url,
            from_number=settings.jambonz_from_number,
            to_number=ev.call_group_number if ev else None,
            tts_audio_base_url=settings.tts_audio_base_url,
            webhook_base_url=settings.jambonz_webhook_base_url,
        )

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
        ack_base_url=router_base_url,
        password=settings.smtp_password,
        use_tls=cfg.smtp.use_tls,
        username=cfg.smtp.username,
        resend_api_key=settings.resend_api_key,
    )

    _router_instance = AlertRouter(
        config=cfg,
        email_adapter=email_adapter,
        signal_adapter=signal_adapter,
        mattermost_adapter=mattermost_adapter,
        slack_adapter=slack_adapter,
        phone_adapter=phone_adapter,
        secret_key=settings.secret_key,
        counter=notification_state_total,
        session_factory=get_session_factory(),
        local_dev=settings.local_dev,
    )

    task = asyncio.create_task(
        listen_for_cases(settings.database_url, _router_instance)
    )
    poll_task: asyncio.Task[None] | None = None
    if signal_adapter and settings.signal_api_url and settings.signal_sender:
        poll_task = asyncio.create_task(
            _poll_signal_reactions(
                settings.signal_api_url,
                settings.signal_sender,
                _router_instance,
                get_session_factory(),
            )
        )
    yield
    task.cancel()
    if poll_task:
        poll_task.cancel()


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
    alert, other_notifications = await alert_router.mark_acked(
        notif.id, acked_by, session
    )
    if alert:
        await alert_router.send_ack_to_all_channels(
            alert, acked_by, other_notifications, session
        )

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
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token"
        )

    alert, other_notifications = await alert_router.mark_acked(
        notification_id, "email_link", session
    )
    if alert is None:
        return HTMLResponse(
            content="<p>This case is already acknowledged or the link has expired.</p>",
            status_code=200,
        )

    await alert_router.send_ack_to_all_channels(
        alert, "email_link", other_notifications, session
    )

    html = (
        f"<h1>✅ Acknowledged</h1>"
        f"<p>Case <strong>{alert.friendly_id}</strong> has been marked as acknowledged.</p>"
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Mattermost button action webhook
# ---------------------------------------------------------------------------


class MattermostActionContext(BaseModel):
    action: str = ""
    case_id: str = ""
    secret: str = ""


class MattermostActionBody(BaseModel):
    user_name: str = ""
    context: MattermostActionContext = MattermostActionContext()


@api.post("/webhook/mattermost/action")
async def mattermost_action(
    body: MattermostActionBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    alert_router: Annotated[AlertRouter, Depends(get_alert_router)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    if (
        settings.mattermost_webhook_secret
        and body.context.secret != settings.mattermost_webhook_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret"
        )

    if body.context.action != "ack":
        return {"update": {"message": "Unknown action"}}

    try:
        case_id = uuid.UUID(body.context.case_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid case_id"
        )

    acked_by = body.user_name or "mattermost"

    # Find the mattermost notification for this case and mark it acked
    result = await session.execute(
        select(Notification).where(
            Notification.case_id == case_id, Notification.channel == "mattermost"
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return {"update": {"message": "Case not found"}}

    alert, other_notifications = await alert_router.mark_acked(
        notif.id, acked_by, session
    )
    if alert:
        await alert_router.send_ack_to_all_channels(
            alert, acked_by, other_notifications, session
        )

    return {"update": {"message": "✅ Acknowledged"}}


# ---------------------------------------------------------------------------
# Internal ACK endpoint (panel / dispatcher / Jambonz → router)
# ---------------------------------------------------------------------------


class InternalAckBody(BaseModel):
    acked_by: str
    notification_id: str | None = None


def _check_internal_secret(
    x_internal_secret: Annotated[str, Header()] = "",
    settings: Settings = Depends(get_settings),
) -> None:
    if (
        settings.router_internal_secret
        and x_internal_secret != settings.router_internal_secret
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@api.post("/internal/ack/{case_id}")
async def internal_ack(
    case_id: uuid.UUID,
    body: InternalAckBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    alert_router: Annotated[AlertRouter, Depends(get_alert_router)],
    _auth: Annotated[None, Depends(_check_internal_secret)],
) -> dict[str, object]:
    if body.notification_id:
        try:
            notification_id = uuid.UUID(body.notification_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid notification_id",
            )

        alert, other_notifications = await alert_router.mark_acked(
            notification_id, body.acked_by, session
        )
        if alert:
            await alert_router.send_ack_to_all_channels(
                alert, body.acked_by, other_notifications, session
            )
        acked_count = 1 if alert else 0
    else:
        notifications = await alert_router.load_sent_notifications(
            str(case_id), session
        )
        alert = await alert_router.load_alert_from_db(str(case_id), session)
        if notifications and alert:
            await alert_router.mark_acked(notifications[0].id, body.acked_by, session)
            await alert_router.send_ack_to_all_channels(
                alert, body.acked_by, notifications, session
            )
        acked_count = len(notifications)

    return {"ok": True, "acked_count": acked_count}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@api.get("/health", tags=["ops"])
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
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
