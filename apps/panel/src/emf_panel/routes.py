from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from authlib.integrations.base_client.errors import MismatchingStateError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from emf_shared.db import get_session

from .auth import oauth, require_conduct_team
from .dispatcher import (
    create_dispatcher_token,
    get_active_device_count,
    revoke_token,
    validate_dispatcher_token,
)
from .models import Case, CaseHistory, Notification
from .settings import Settings, get_settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")

VALID_TRANSITIONS: dict[str, set[str]] = {
    "new": {"assigned"},
    "assigned": {"in_progress", "new", "closed"},
    "in_progress": {"action_needed", "decision_needed", "closed"},
    "action_needed": {"in_progress", "decision_needed", "closed"},
    "decision_needed": {"closed", "in_progress"},
    "closed": set(),
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
        # State already consumed (double request / browser retry).
        # If the first hit already stored the user, just continue.
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
# Panel routes — conduct team
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def case_list(
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: str | None = Query(None, alias="status"),
    urgency: str | None = None,
    assignee: str | None = None,
    tag: str | None = None,
) -> HTMLResponse:
    stmt = select(Case).order_by(Case.created_at.desc())
    if status_filter:
        stmt = stmt.where(Case.status == status_filter)
    if urgency:
        stmt = stmt.where(Case.urgency == urgency)
    if assignee:
        stmt = stmt.where(Case.assignee == assignee)
    if tag:
        stmt = stmt.where(Case.tags.contains([tag]))
    result = await session.execute(stmt)
    cases = result.scalars().all()
    return templates.TemplateResponse(request, "cases.html", {
            "request": request,
            "cases": cases,
            "user": user,
            "valid_transitions": VALID_TRANSITIONS,
        },
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail(
    case_id: uuid.UUID,
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
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
    return templates.TemplateResponse(request, "case_detail.html", {
            "request": request,
            "case": case,
            "history": history,
            "user": user,
            "valid_next_statuses": sorted(valid_next),
        },
    )


class StatusTransition(BaseModel):
    status: str


@router.patch("/api/cases/{case_id}/status")
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


@router.patch("/api/cases/{case_id}/assignee")
async def update_assignee(
    case_id: uuid.UUID,
    body: AssigneeUpdate,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
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
    return {"assignee": body.assignee}


class TagsUpdate(BaseModel):
    tags: list[str]


@router.patch("/api/cases/{case_id}/tags")
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


@router.get("/api/tags")
async def list_tags(
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    result = await session.execute(
        text("SELECT DISTINCT jsonb_array_elements_text(tags) AS tag FROM forms.cases ORDER BY tag")
    )
    return [row[0] for row in result.fetchall()]


class DispatcherSessionRequest(BaseModel):
    send_to: str | None = None


@router.post("/api/dispatcher-session")
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


@router.post("/api/dispatcher-session/{jti}/revoke")
async def revoke_dispatcher_session(
    jti: str,
    _user: Annotated[dict[str, object], Depends(require_conduct_team)],
) -> dict[str, bool]:
    revoke_token(jti)
    return {"ok": True}


@router.get("/dispatcher-share", response_class=HTMLResponse)
async def dispatcher_share_page(
    request: Request,
    user: Annotated[dict[str, object], Depends(require_conduct_team)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return templates.TemplateResponse(request, "dispatcher_share.html", {"request": request, "user": user, "settings": settings},
    )


# ---------------------------------------------------------------------------
# Dispatcher routes — token-authenticated
# ---------------------------------------------------------------------------


@router.get("/dispatcher", response_class=HTMLResponse)
async def dispatcher_view(
    request: Request,
    token: str = Query(...),
    device_id: Annotated[str | None, Cookie()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
    session: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> HTMLResponse:
    if settings is None:
        settings = get_settings()
    dev_id = device_id or str(uuid.uuid4())
    validate_dispatcher_token(token, dev_id, settings.secret_key)
    result = await session.execute(
        select(Case)
        .where(Case.assignee.is_(None))
        .order_by(Case.urgency.desc(), Case.created_at.desc())
    )
    cases = result.scalars().all()
    response = templates.TemplateResponse(request, "dispatcher.html", {"request": request, "cases": cases, "token": token},
    )
    response.set_cookie("device_id", dev_id, httponly=True, samesite="strict")
    return response


@router.get("/dispatcher/cases")
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
        }
        for c in cases
    ]


class AckBody(BaseModel):
    acked_by: str = "dispatcher"


@router.post("/api/dispatcher/ack/{case_id}")
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
    return {"ok": True}


@router.post("/api/dispatcher/trigger/{case_id}")
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
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
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
