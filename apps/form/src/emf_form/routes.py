from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import anyio
import emf_shared
import httpx
from emf_shared.db import get_session
from emf_shared.friendly_id import generate
from emf_shared.phase import Phase, current_phase, events_for_form, is_active_routing_window
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Case, IdempotencyToken
from .schemas import CaseSubmission
from .settings import Settings, get_settings

router = APIRouter()
log = logging.getLogger(__name__)

# In-memory limiter for decorator metadata; replaced with Redis-backed instance
# at startup when REDIS_URL is set (see main.py lifespan).
limiter = Limiter(key_func=get_remote_address)


def build_limiter(redis_url: str) -> Limiter:
    """Return a Redis-backed limiter; falls back to in-memory when url is empty."""
    if redis_url:
        return Limiter(key_func=get_remote_address, storage_uri=redis_url)
    return Limiter(key_func=get_remote_address)


try:
    _VERSION = importlib.metadata.version("emf-form")
except importlib.metadata.PackageNotFoundError:
    _VERSION = os.environ.get("BUILD_VERSION", "dev")

_shared_templates_dir = str(Path(emf_shared.__file__).parent / "templates")
templates = Jinja2Templates(directory="templates")
_original_loader = templates.env.loader
if _original_loader is None:
    raise RuntimeError("Jinja2Templates did not configure a loader")
templates.env.loader = ChoiceLoader([_original_loader, FileSystemLoader(_shared_templates_dir)])

_SAFE_BROWSING_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"


async def _scan_with_clamd(data: bytes) -> str | None:
    try:
        import pyclamd

        cd = pyclamd.ClamdNetworkSocket(host="clamav", port=3310, timeout=10)
        result = await asyncio.to_thread(cd.scan_stream, data)
        if result:
            return str(next(iter(result.values()))[1])
        return None
    except Exception:
        log.warning("clamd not reachable — skipping AV scan")
        return None


async def _clamd_ping() -> bool:
    try:
        import pyclamd

        cd = pyclamd.ClamdNetworkSocket(host="clamav", port=3310, timeout=3)
        return bool(await asyncio.to_thread(cd.ping))
    except Exception:
        return False


async def _check_urls_safe_browsing(urls: list[str], api_key: str) -> list[str]:
    payload: dict[str, object] = {
        "client": {"clientId": "emf-conduct", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_SAFE_BROWSING_URL, params={"key": api_key}, json=payload)
        if resp.status_code != 200:
            log.warning("Safe Browsing API returned %s — skipping URL check", resp.status_code)
            return []
        raw = resp.json()
        if not isinstance(raw, dict):
            return []
        matches = raw.get("matches", [])
        if not isinstance(matches, list):
            return []
        unsafe: list[str] = []
        for m in matches:
            if isinstance(m, dict):
                threat = m.get("threat")
                if isinstance(threat, dict):
                    url = threat.get("url")
                    if isinstance(url, str):
                        unsafe.append(url)
        return unsafe
    except Exception:
        log.warning("Safe Browsing API request failed — skipping URL check", exc_info=True)
        return []


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
    today = datetime.now(tz=UTC).date().isoformat()
    return templates.TemplateResponse(
        request,
        "form.html",
        {
            "phase": phase,
            "config": config,
            "events": events,
            "is_active_routing_window": active,
            "is_event_time": phase == Phase.EVENT_TIME,
            "current_event_name": current_event_name,
            "today": today,
        },
    )


@router.post(
    "/api/submit",
    status_code=status.HTTP_201_CREATED,
    responses={
        # Our domain-level 422s return {"detail": "string"}, not ValidationError[].
        # Override the auto-generated schema so schemathesis doesn't validate detail shape.
        400: {"description": "Unsafe URL or invalid submission"},
        422: {"content": {"application/json": {"schema": {}}}},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("15/10 seconds")
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
    phase = current_phase(config)

    valid_events = {e.name for e in config.events}
    if submission.event_name not in valid_events:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown event: {submission.event_name}",
        )

    if submission.urgency not in set(config.urgency_levels):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid urgency: {submission.urgency}",
        )

    if submission.can_contact:
        has_email = bool(submission.reporter.email)
        has_phone = bool(submission.reporter.phone)
        if phase == Phase.EVENT_TIME:
            if not has_email and not has_phone:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="An email address or phone number is required when you have agreed to be contacted.",  # noqa: E501
                )
        else:
            if not has_email:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="An email address is required when you have agreed to be contacted.",
                )

    if x_idempotency_key:
        existing_token = await session.get(IdempotencyToken, x_idempotency_key)
        if existing_token is not None:
            row = await session.execute(
                select(Case.friendly_id).where(Case.id == existing_token.case_id)
            )
            friendly = row.scalar_one_or_none() or x_idempotency_key[:8]
            return JSONResponse(
                content={"case_id": str(existing_token.case_id), "friendly_id": friendly},
                status_code=status.HTTP_200_OK,
            )

    if submission.media_links and settings.google_safe_browsing_key:
        unsafe = await _check_urls_safe_browsing(
            submission.media_links, settings.google_safe_browsing_key
        )
        if unsafe:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "One or more links could not be verified as safe. "
                    "Please remove them and resubmit."
                ),
            )

    case_id = uuid.uuid4()
    form_data: dict[str, object] = submission.model_dump(
        mode="json", exclude={"website", "urgency", "event_name"}
    )

    friendly_id: str | None = None
    for _ in range(5):
        candidate = generate()
        case = Case(
            id=case_id,
            friendly_id=candidate,
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
        try:
            await session.flush()
            friendly_id = candidate
            break
        except IntegrityError:
            await session.rollback()

    if friendly_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not generate unique case ID",
        )

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


_IMAGE_MAGIC: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",
}

_WEBP_SIG = b"WEBP"


def _detect_image_ext(header: bytes) -> str | None:
    for magic, ext in _IMAGE_MAGIC.items():
        if header[: len(magic)].startswith(magic):
            if ext == "webp" and len(header) >= 12 and header[8:12] == _WEBP_SIG:
                return "webp"
            elif ext != "webp":
                return ext
    return None


@router.post(
    "/attachments",
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Virus detected or malformed upload"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported media type"},
    },
)
async def upload_attachment(
    case_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    header = await file.read(12)
    ext = _detect_image_ext(header)
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, GIF, and WebP images are accepted",
        )
    case_result = await session.execute(select(Case).where(Case.id == case_id))
    if case_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    rest = await file.read()
    total = len(header) + len(rest)
    cfg = settings.app_config
    if total > cfg.attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {cfg.attachment_max_bytes // (1024 * 1024)} MB)",
        )
    virus = await _scan_with_clamd(header + rest)
    if virus:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Upload rejected by virus scanner: {virus}",
        )

    case_dir = settings.attachment_dir / str(case_id)
    await anyio.to_thread.run_sync(lambda: case_dir.mkdir(parents=True, exist_ok=True))
    existing = await anyio.to_thread.run_sync(
        lambda: (
            list(case_dir.glob("*.jpg"))
            + list(case_dir.glob("*.png"))
            + list(case_dir.glob("*.gif"))
            + list(case_dir.glob("*.webp"))
        )
    )
    if len(existing) >= cfg.attachment_max_per_case:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Maximum {cfg.attachment_max_per_case} attachments per case",
        )
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = case_dir / filename
    data = header + rest
    await anyio.to_thread.run_sync(lambda: dest.write_bytes(data))
    return {"id": filename, "case_id": str(case_id)}


@router.get("/health", tags=["ops"])
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    clamd_status = "ok" if await _clamd_ping() else "unavailable"
    sb_status = "configured" if settings.google_safe_browsing_key else "not_configured"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "checks": {
            "database": db_status,
            "clamav": clamd_status,
            "safe_browsing": sb_status,
        },
        "version": _VERSION,
    }
