from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from emf_shared.db import get_session
from emf_shared.friendly_id import generate_unique
from emf_shared.phase import current_phase, events_for_form, is_active_routing_window
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Case, IdempotencyToken
from .schemas import CaseSubmission
from .settings import Settings, get_settings

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def get_form(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    config = settings.app_config
    phase = current_phase(config)
    active = settings.local_dev or is_active_routing_window(config)
    events = events_for_form(config)
    current_event_name = events[0].name if events else ""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    return templates.TemplateResponse(
        request,
        "form.html",
        {
            "phase": phase,
            "config": config,
            "events": events,
            "is_active_routing_window": active,
            "current_event_name": current_event_name,
            "today": today,
        },
    )


@router.post("/api/submit", status_code=status.HTTP_201_CREATED)
async def submit_form(
    request: Request,
    submission: CaseSubmission,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    if submission.website:
        fake_id = str(uuid.uuid4())
        return JSONResponse(
            content={"case_id": fake_id, "friendly_id": "silent-drop"},
            status_code=status.HTTP_200_OK,
        )

    config = settings.app_config
    valid_events = {e.name for e in config.events}
    if submission.event_name not in valid_events:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown event: {submission.event_name}",
        )

    if submission.urgency not in set(config.urgency_levels):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid urgency: {submission.urgency}",
        )

    if x_idempotency_key:
        existing_token = await session.get(IdempotencyToken, x_idempotency_key)
        if existing_token is not None:
            existing_case = await session.get(Case, existing_token.case_id)
            friendly = existing_case.friendly_id if existing_case else x_idempotency_key[:8]
            return JSONResponse(
                content={"case_id": str(existing_token.case_id), "friendly_id": friendly},
                status_code=status.HTTP_200_OK,
            )

    existing_ids_result = await session.execute(select(Case.friendly_id))
    existing_ids: set[str] = set(existing_ids_result.scalars().all())

    phase = current_phase(config)
    case_id = uuid.uuid4()
    friendly_id = generate_unique(existing_ids, str(case_id))

    form_data: dict[str, object] = submission.model_dump(
        mode="json", exclude={"website", "location", "urgency", "event_name"}
    )

    case = Case(
        id=case_id,
        friendly_id=friendly_id,
        event_name=submission.event_name,
        urgency=submission.urgency,
        phase=str(phase),
        form_data=form_data,
        location_hint=submission.location.text if submission.location else None,
        status="new",
        tags=[],
    )
    session.add(case)

    if x_idempotency_key:
        token = IdempotencyToken(
            token=x_idempotency_key,
            case_id=case_id,
        )
        session.add(token)

    await session.flush()
    await session.execute(
        text("SELECT pg_notify('new_case', :payload)"),
        {"payload": str(case_id)},
    )
    await session.commit()

    return JSONResponse(
        content={"case_id": str(case_id), "friendly_id": friendly_id},
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/success", response_class=HTMLResponse)
async def success_page(
    request: Request,
    friendly_id: str = "",
    already_submitted: bool = False,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "success.html",
        {
            "friendly_id": friendly_id,
            "already_submitted": already_submitted,
        },
    )


@router.get("/health")
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
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
