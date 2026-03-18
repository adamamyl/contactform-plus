from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, cast

import httpx
import redis.asyncio as aioredis
from authlib.integrations.base_client.errors import MismatchingStateError
from emf_shared.config import EventConfig
from emf_shared.db import get_session
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import case as sa_case
from sqlalchemy import exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import oauth, require_conduct_team
from .dispatcher import (
    create_dispatcher_token,
    revoke_token,
    validate_dispatcher_token,
)
from .models import Case, CaseHistory, Notification
from .settings import Settings, get_settings

log = logging.getLogger(__name__)


def _map_base_url(settings: Settings) -> str:
    cfg = settings.app_config
    if cfg.site_map:
        return cfg.site_map.map_url.rstrip("/")
    if cfg.domains and cfg.domains.map:
        return f"https://{cfg.domains.map}"
    return "https://map.emf-forms.internal"


router = APIRouter()
templates = Jinja2Templates(directory="templates")

_ASSIGNEES_KEY = "panel:assignees"


async def get_redis(settings: Annotated[Settings, Depends(get_settings)]) -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _current_active_event(events: list[EventConfig], today: date | None = None) -> str | None:
    today = today or date.today()
    for ev in events:
        pad = ev.signal_padding
        start = ev.start_date - timedelta(days=pad.before_event_days)
        end = ev.end_date + timedelta(days=pad.after_event_days)
        if start <= today <= end:
            return ev.name
    return None


VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"assigned"},
    "assigned": {"in_progress", "new", "closed"},
    "in_progress": {"action_needed", "decision_needed", "closed"},
    "action_needed": {"in_progress", "decision_needed", "closed"},
    "decision_needed": {"closed", "in_progress"},
    "closed": set(),
}

STATUS_EMOJI: dict[str, str] = {
    "new": "🆕",
    "assigned": "👤",
    "in_progress": "🔄",
    "action_needed": "⚠️",
    "decision_needed": "🤔",
    "closed": "✅",
}


def _case_links(case_id: uuid.UUID) -> dict[str, str]:
    base = f"/api/v1/cases/{case_id}"
    return {
        "self": base,
        "history": f"{base}/history",
        "status": f"{base}/status",
        "urgency": f"{base}/urgency",
        "assignee": f"{base}/assignee",
        "tags": f"{base}/tags",
        "ack": f"{base}/ack",
        "calls": f"{base}/calls",
    }


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    redirect_uri = str(request.url_for("auth_callback"))
    return cast(RedirectResponse, await oauth.emf.authorize_redirect(request, redirect_uri))


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request) -> RedirectResponse:
    try:
        token = await oauth.emf.authorize_access_token(request)
    except MismatchingStateError:
        if request.session.get("user"):
            return RedirectResponse(url="/", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    user: dict[str, object] = token.get("userinfo", {})
    request.session["user"] = user
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ---------------------------------------------------------------------------
# Panel HTML routes — conduct team
# ---------------------------------------------------------------------------


_URGENCY_ORDER = sa_case(
    {"urgent": 0, "high": 1, "medium": 2, "low": 3},
    value=Case.urgency,
    else_=9,
)

_SORT_COLS = {
    "id": Case.friendly_id,
    "urgency": _URGENCY_ORDER,
    "status": Case.status,
    "assignee": Case.assignee,
    "submitted": Case.created_at,
}


@router.get("/", response_class=HTMLResponse)
async def case_list(
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    status_filter: Annotated[list[str], Query(alias="status")] = [],  # noqa: B006
    urgency_filter: Annotated[list[str], Query(alias="urgency")] = [],  # noqa: B006
    assignee: str | None = None,
    tag: str | None = None,
    sort: str = "submitted",
    order: str = "desc",
) -> HTMLResponse:
    if assignee == "me":
        assignee = str(user.get("preferred_username", ""))
    sort_col = _SORT_COLS.get(sort, Case.created_at)
    sort_expr = sort_col.desc() if order == "desc" else sort_col.asc()
    stmt = select(Case).order_by(sort_expr)
    if status_filter:
        stmt = stmt.where(Case.status.in_(status_filter))
    if urgency_filter:
        stmt = stmt.where(Case.urgency.in_(urgency_filter))
    if assignee:
        stmt = stmt.where(Case.assignee == assignee)
    if tag:
        stmt = stmt.where(Case.tags.contains([tag]))

    def make_sort_url(col: str) -> str:
        new_order = "desc" if sort == col and order == "asc" else "asc"
        return str(request.url.include_query_params(sort=col, order=new_order))

    result = await session.execute(stmt)
    cases = result.scalars().all()
    map_urls: dict[uuid.UUID, str] = {}
    for c in cases:
        fd = c.form_data
        if not isinstance(fd, dict):
            continue
        loc = fd.get("location")
        if not isinstance(loc, dict):
            continue
        lat = loc.get("lat")
        lon = loc.get("lon")
        if lat is not None and lon is not None:
            map_urls[c.id] = f"{_map_base_url(settings)}/?marker={lat},{lon}#16/{lat}/{lon}"
    case_ids = [c.id for c in cases]
    notif_states: dict[uuid.UUID, str] = {}
    if case_ids:
        notif_rows = await session.execute(
            select(
                Notification.case_id,
                sa_case(
                    (func.count().filter(Notification.state != "acked") > 0, "nack"),
                    else_="acked",
                ).label("notif_state"),
            )
            .where(Notification.case_id.in_(case_ids))
            .group_by(Notification.case_id)
        )
        notif_states = {row.case_id: row.notif_state for row in notif_rows}
    return templates.TemplateResponse(
        request,
        "cases.html",
        {
            "request": request,
            "cases": cases,
            "user": user,
            "valid_transitions": VALID_TRANSITIONS,
            "selected_statuses": status_filter,
            "selected_urgencies": urgency_filter,
            "map_urls": map_urls,
            "notif_states": notif_states,
            "sort": sort,
            "order": order,
            "make_sort_url": make_sort_url,
            "status_emoji": STATUS_EMOJI,
        },
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail(
    case_id: uuid.UUID,
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    hist_result = await session.execute(
        select(CaseHistory)
        .where(CaseHistory.case_id == case_id)
        .order_by(CaseHistory.changed_at.asc())
    )
    history = hist_result.scalars().all()
    valid_next = VALID_TRANSITIONS.get(case.status, set())
    attach_dir = Path(settings.attachment_dir) / str(case_id)
    attachments: list[str] = []
    if attach_dir.is_dir():
        attachments = [
            f.name
            for f in sorted(attach_dir.iterdir())
            if f.suffix.lower() in {".jpg", ".png", ".gif", ".webp"}
        ]
    return templates.TemplateResponse(
        request,
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "history": history,
            "user": user,
            "valid_next_statuses": sorted(valid_next),
            "attachments": attachments,
            "status_emoji": STATUS_EMOJI,
            "urgency_levels": settings.app_config.urgency_levels,
            "map_base_url": _map_base_url(settings),
        },
    )


# ---------------------------------------------------------------------------
# API v1 — cases
# ---------------------------------------------------------------------------


@router.get("/api/v1/cases")
async def api_list_cases(
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[list[str], Query(alias="status")] = [],  # noqa: B006
    urgency_filter: Annotated[list[str], Query(alias="urgency")] = [],  # noqa: B006
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    stmt = select(Case).order_by(Case.created_at.desc())
    if status_filter:
        stmt = stmt.where(Case.status.in_(status_filter))
    if urgency_filter:
        stmt = stmt.where(Case.urgency.in_(urgency_filter))
    total_result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    total: int = total_result.scalar_one()
    result = await session.execute(stmt.limit(limit).offset(offset))
    items = [
        {
            "id": str(c.id),
            "friendly_id": c.friendly_id,
            "event_name": c.event_name,
            "urgency": c.urgency,
            "status": c.status,
            "assignee": c.assignee,
            "tags": c.tags,
            "location_hint": c.location_hint,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "_links": _case_links(c.id),
        }
        for c in result.scalars().all()
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/api/v1/cases/{case_id}")
async def api_get_case(
    case_id: uuid.UUID,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    return {
        "id": str(case.id),
        "friendly_id": case.friendly_id,
        "event_name": case.event_name,
        "urgency": case.urgency,
        "status": case.status,
        "assignee": case.assignee,
        "tags": case.tags,
        "location_hint": case.location_hint,
        "form_data": case.form_data,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "_links": _case_links(case_id),
    }


@router.get("/api/v1/cases/{case_id}/history")
async def api_case_history(
    case_id: uuid.UUID,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, object]]:
    result = await session.execute(
        select(CaseHistory)
        .where(CaseHistory.case_id == case_id)
        .order_by(CaseHistory.changed_at.asc())
    )
    return [
        {
            "id": h.id,
            "changed_by": h.changed_by,
            "field": h.field,
            "old_value": h.old_value,
            "new_value": h.new_value,
            "changed_at": h.changed_at.isoformat(),
        }
        for h in result.scalars().all()
    ]


class StatusTransition(BaseModel):
    status: str


@router.patch("/api/v1/cases/{case_id}/status")
async def transition_status(
    case_id: uuid.UUID,
    body: StatusTransition,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    allowed = VALID_TRANSITIONS.get(case.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition '{case.status}' → '{body.status}'",
        )
    old_status = case.status
    await session.execute(
        update(Case)
        .where(Case.id == case_id)
        .values(status=body.status, updated_at=datetime.now(tz=UTC))
    )
    session.add(
        CaseHistory(
            case_id=case_id,
            changed_by=str(user.get("preferred_username", "unknown")),
            field="status",
            old_value=old_status,
            new_value=body.status,
        )
    )
    await session.commit()
    return {"status": body.status}


class AssigneeUpdate(BaseModel):
    assignee: str | None


@router.patch("/api/v1/cases/{case_id}/assignee")
async def update_assignee(
    case_id: uuid.UUID,
    body: AssigneeUpdate,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, str | None]:
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    old = case.assignee
    await session.execute(
        update(Case)
        .where(Case.id == case_id)
        .values(assignee=body.assignee, updated_at=datetime.now(tz=UTC))
    )
    session.add(
        CaseHistory(
            case_id=case_id,
            changed_by=str(user.get("preferred_username", "unknown")),
            field="assignee",
            old_value=old,
            new_value=body.assignee,
        )
    )
    await session.commit()
    if body.assignee:
        await redis.sadd(_ASSIGNEES_KEY, body.assignee)  # type: ignore[misc]
    return {"assignee": body.assignee}


class TagsUpdate(BaseModel):
    tags: list[str]


@router.patch("/api/v1/cases/{case_id}/tags")
async def update_tags(
    case_id: uuid.UUID,
    body: TagsUpdate,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[str]]:
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    old_tags = case.tags
    await session.execute(
        update(Case)
        .where(Case.id == case_id)
        .values(tags=body.tags, updated_at=datetime.now(tz=UTC))
    )
    session.add(
        CaseHistory(
            case_id=case_id,
            changed_by=str(user.get("preferred_username", "unknown")),
            field="tags",
            old_value=str(old_tags),
            new_value=str(body.tags),
        )
    )
    await session.commit()
    return {"tags": body.tags}


class UrgencyUpdate(BaseModel):
    urgency: str


@router.patch("/api/v1/cases/{case_id}/urgency")
async def update_urgency(
    case_id: uuid.UUID,
    body: UrgencyUpdate,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    cfg = settings.app_config
    if body.urgency not in cfg.urgency_levels:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid urgency '{body.urgency}'; must be one of {cfg.urgency_levels}",
        )
    case = await session.get(Case, case_id)
    if not case:
        raise HTTPException(status_code=404)
    old_urgency = case.urgency
    await session.execute(
        update(Case)
        .where(Case.id == case_id)
        .values(urgency=body.urgency, updated_at=datetime.now(tz=UTC))
    )
    session.add(
        CaseHistory(
            case_id=case_id,
            changed_by=str(user.get("preferred_username", "unknown")),
            field="urgency",
            old_value=old_urgency,
            new_value=body.urgency,
        )
    )
    await session.commit()
    return {"urgency": body.urgency}


# ---------------------------------------------------------------------------
# API v1 — lookup lists
# ---------------------------------------------------------------------------


@router.get("/api/v1/assignees")
async def list_assignees(
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> list[str]:
    members: set[str] = await redis.smembers(_ASSIGNEES_KEY)  # type: ignore[misc]
    return sorted(members)


@router.get("/api/v1/tags")
async def list_tags(
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    result = await session.execute(
        text("SELECT DISTINCT jsonb_array_elements_text(tags) AS tag FROM forms.cases ORDER BY tag")
    )
    return [row[0] for row in result.fetchall()]


# ---------------------------------------------------------------------------
# API v1 — case actions
# ---------------------------------------------------------------------------


async def _notify_router_ack(case_id: uuid.UUID, acked_by: str, settings: Settings) -> None:
    if not settings.router_internal_url:
        return
    headers: dict[str, str] = {}
    if settings.router_internal_secret:
        headers["X-Internal-Secret"] = settings.router_internal_secret
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{settings.router_internal_url}/internal/ack/{case_id}",
                json={"acked_by": acked_by},
                headers=headers,
            )
    except Exception:
        log.warning("Failed to notify router of ACK for case %s", case_id)


@router.post("/api/v1/cases/{case_id}/ack")
async def admin_ack(
    case_id: uuid.UUID,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, bool]:
    username = str(user.get("preferred_username", "admin"))
    now = datetime.now(tz=UTC)
    await session.execute(
        update(Notification)
        .where(Notification.case_id == case_id, Notification.state != "acked")
        .values(state="acked", acked_at=now, acked_by=username)
    )
    await session.execute(
        update(Case).where(Case.id == case_id).values(assignee=username, updated_at=now)
    )
    await session.commit()
    await redis.sadd(_ASSIGNEES_KEY, username)  # type: ignore[misc]
    await _notify_router_ack(case_id, username, settings)
    return {"ok": True}


@router.post("/api/v1/cases/{case_id}/calls")
async def admin_trigger_call(
    case_id: uuid.UUID,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    await session.execute(
        text("SELECT pg_notify('new_case', :payload)"),
        {"payload": str(case_id)},
    )
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API v1 — dispatcher sessions
# ---------------------------------------------------------------------------


class DispatcherSessionRequest(BaseModel):
    send_to: str | None = None


@router.post("/api/v1/dispatcher/sessions")
async def create_dispatcher_session(
    body: DispatcherSessionRequest,
    request: Request,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    ttl = settings.dispatcher_session_ttl_hours
    token = create_dispatcher_token(settings.secret_key, ttl)
    url = f"{settings.base_url}/dispatcher?token={token}"
    return {"url": url, "expires_in_hours": ttl}


@router.delete("/api/v1/dispatcher/sessions/{jti}", status_code=204)
async def revoke_dispatcher_session(
    jti: str,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
) -> None:
    revoke_token(jti)


# ---------------------------------------------------------------------------
# Dispatcher HTML + API v1 dispatcher routes — token-authenticated
# ---------------------------------------------------------------------------


@router.get("/dispatcher-share", response_class=HTMLResponse)
async def dispatcher_share_page(
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dispatcher_share.html",
        {"request": request, "user": user, "settings": settings},
    )


@router.get("/dispatcher", response_class=HTMLResponse)
async def dispatcher_view(
    request: Request,
    token: str = Query(...),
    show_acked: bool = Query(False),
    event_override: str | None = Query(None, alias="event"),
    device_id: Annotated[str | None, Cookie()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> HTMLResponse:
    if settings is None:
        settings = get_settings()
    dev_id = device_id or str(uuid.uuid4())
    validate_dispatcher_token(token, dev_id, settings.secret_key)
    cfg = settings.app_config
    known_event_names = {e.name for e in cfg.events}
    active_event: str | None
    if event_override and event_override in known_event_names:
        active_event = event_override
    else:
        active_event = settings.current_event_override or _current_active_event(cfg.events)
    stmt = (
        select(Case)
        .where(Case.assignee.is_(None))
        .order_by(Case.urgency.desc(), Case.created_at.desc())
    )
    if active_event:
        stmt = stmt.where(Case.event_name == active_event)
    if not show_acked:
        stmt = stmt.where(
            ~exists(
                select(Notification.id).where(
                    Notification.case_id == Case.id,
                    Notification.state == "acked",
                )
            )
        )
    result = await session.execute(stmt)
    cases = result.scalars().all()
    response = templates.TemplateResponse(
        request,
        "dispatcher.html",
        {
            "request": request,
            "cases": cases,
            "token": token,
            "active_event": active_event,
            "show_acked": show_acked,
            "map_base_url": _map_base_url(settings),
        },
    )
    response.set_cookie("device_id", dev_id, httponly=True, samesite="strict")
    return response


@router.get("/api/v1/dispatcher/cases")
async def dispatcher_cases(
    token: str = Query(...),
    show_all: bool = Query(False, alias="all"),
    device_id: Annotated[str | None, Cookie()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> list[dict[str, object]]:
    if settings is None:
        settings = get_settings()
    dev_id = device_id or str(uuid.uuid4())
    validate_dispatcher_token(token, dev_id, settings.secret_key)
    stmt = select(Case).order_by(Case.urgency.desc(), Case.created_at.desc())
    if not show_all:
        stmt = stmt.where(Case.assignee.is_(None))
    result = await session.execute(stmt)
    cases = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "friendly_id": c.friendly_id,
            "urgency": c.urgency,
            "status": c.status,
            "location_hint": c.location_hint,
            "assignee": c.assignee,
            "created_at": c.created_at.isoformat(),
            "_links": _case_links(c.id),
        }
        for c in cases
    ]


class AckBody(BaseModel):
    acked_by: str = "dispatcher"


@router.post("/api/v1/dispatcher/cases/{case_id}/ack")
async def dispatcher_ack(
    case_id: uuid.UUID,
    body: AckBody,
    token: str = Query(...),
    device_id: Annotated[str | None, Cookie()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> dict[str, bool]:
    if settings is None:
        settings = get_settings()
    dev_id = device_id or str(uuid.uuid4())
    validate_dispatcher_token(token, dev_id, settings.secret_key)
    now = datetime.now(tz=UTC)
    await session.execute(
        update(Notification)
        .where(Notification.case_id == case_id, Notification.state != "acked")
        .values(state="acked", acked_at=now, acked_by=body.acked_by)
    )
    await session.commit()
    await _notify_router_ack(case_id, "dispatcher", settings)
    return {"ok": True}


@router.post("/api/v1/dispatcher/cases/{case_id}/calls")
async def dispatcher_trigger(
    case_id: uuid.UUID,
    token: str = Query(...),
    device_id: Annotated[str | None, Cookie()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> dict[str, bool]:
    if settings is None:
        settings = get_settings()
    dev_id = device_id or str(uuid.uuid4())
    validate_dispatcher_token(token, dev_id, settings.secret_key)
    await session.execute(
        text("SELECT pg_notify('new_case', :payload)"),
        {"payload": str(case_id)},
    )
    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Attachment proxy — team-auth required
# ---------------------------------------------------------------------------

_ATTACHMENT_MIME: dict[str, str] = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


@router.get("/cases/{case_id}/attachments/{filename}")
async def serve_attachment(
    case_id: uuid.UUID,
    filename: str,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_type = _ATTACHMENT_MIME.get(ext)
    if media_type is None:
        raise HTTPException(status_code=400, detail="Unknown file type")
    path = Path(settings.attachment_dir) / str(case_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=media_type)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health", tags=["ops"])
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, object]:
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "checks": {"database": db_status},
        "version": "0.1.0",
    }
