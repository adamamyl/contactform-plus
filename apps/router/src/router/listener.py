from __future__ import annotations

import asyncio
import logging
import uuid

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy import func, select

from emf_shared.db import get_session
from router.alert_router import AlertRouter
from router.models import Notification

log = logging.getLogger(__name__)


async def listen_for_cases(dsn: str, router: AlertRouter) -> None:
    """Long-lived asyncpg connection that LISTENs for new_case notifications."""
    # asyncpg requires plain postgresql:// scheme; strip SQLAlchemy driver prefix
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    while True:
        try:
            conn: asyncpg.Connection[asyncpg.Record] = await asyncpg.connect(dsn)
            log.info("Listener connected; registering LISTEN new_case")

            async def _on_notify(
                connection: asyncpg.Connection[asyncpg.Record],
                pid: int,
                channel: str,
                payload: str,
            ) -> None:
                log.info("NOTIFY new_case: %s", payload)
                asyncio.create_task(_handle_new_case(payload, router))

            await conn.add_listener("new_case", _on_notify)
            # Poll until the connection drops
            while not conn.is_closed():
                await asyncio.sleep(5)
        except Exception:
            log.exception("Listener error; reconnecting in 5 s")
            await asyncio.sleep(5)


async def _handle_new_case(case_id: str, router: AlertRouter) -> None:
    try:
        async for session in get_session():
            result = await session.execute(
                select(func.count()).where(Notification.case_id == uuid.UUID(case_id))
            )
            if result.scalar_one() > 0:
                log.info("Case %s already has notifications; skipping", case_id)
                return
            alert = await router.load_alert_from_db(case_id, session)
            if alert is None:
                log.warning("Case %s not found in cases_router view", case_id)
                return
            await router.route(alert, session)
    except Exception:
        log.exception("Error handling new_case %s", case_id)
