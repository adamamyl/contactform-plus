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
    """Sync wrapper around asyncpg for use in synchronous Playwright tests.

    Runs each query in a fresh thread so asyncio.run() doesn't conflict with
    pytest-asyncio's event loop.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def fetchrow(self, query: str, *args: Any) -> Any:
        async def _run() -> Any:
            conn: asyncpg.Connection = await asyncpg.connect(self._dsn)
            try:
                return await conn.fetchrow(query, *args)
            finally:
                await conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run()).result()


@pytest.fixture()
def db() -> SyncDB:
    dsn = os.environ.get("E2E_DB_URL", "postgresql://emf_forms_admin@localhost:5432/emf_forms")
    return SyncDB(dsn)
