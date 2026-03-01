from __future__ import annotations

import os
from collections.abc import Iterator

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
