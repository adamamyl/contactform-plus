from __future__ import annotations

import asyncio
import concurrent.futures
import os
from collections.abc import Iterator
from typing import Any

import asyncpg
import httpx
import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: end-to-end test against a running stack")


@pytest.fixture(scope="session")
def form_base_url() -> str:
    url = os.environ.get("FORM_BASE_URL", "")
    if not url:
        pytest.skip("FORM_BASE_URL not set — bring up the e2e stack first")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def panel_base_url() -> str:
    url = os.environ.get("PANEL_BASE_URL", "")
    if not url:
        pytest.skip("PANEL_BASE_URL not set — bring up the e2e stack first")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def form_client(form_base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=form_base_url, follow_redirects=False, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session")
def panel_client(panel_base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=panel_base_url, follow_redirects=False, timeout=10.0) as c:
        yield c


class SyncDB:
    """Sync wrapper around asyncpg using a persistent connection in a dedicated thread.

    Opens a single asyncpg connection for the lifetime of the object so each
    fetchrow() call reuses it rather than creating a new thread and connection.
    Call connect() before use, close() when done.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Create a dedicated event loop in the background thread.
        self._loop: asyncio.AbstractEventLoop = self._executor.submit(
            asyncio.new_event_loop
        ).result()
        self._conn: asyncpg.Connection[asyncpg.Record] | None = None

    def _run(self, coro: Any) -> Any:
        return self._executor.submit(self._loop.run_until_complete, coro).result()

    def connect(self) -> None:
        self._conn = self._run(asyncpg.connect(self._dsn))

    def fetchrow(self, query: str, *args: Any) -> Any:
        assert self._conn is not None

        async def _fetch() -> Any:
            try:
                return await self._conn.fetchrow(query, *args)
            except asyncpg.PostgresError:
                await self._conn.execute("ROLLBACK")
                raise

        return self._run(_fetch())

    def close(self) -> None:
        if self._conn is not None:
            self._run(self._conn.close())
        self._executor.shutdown(wait=True)


@pytest.fixture(scope="session")
def db() -> Iterator[SyncDB]:
    dsn = os.environ.get("E2E_DB_URL", "postgresql://emf_forms_admin@localhost:5432/emf_forms")
    sync_db = SyncDB(dsn)
    sync_db.connect()
    yield sync_db
    sync_db.close()
