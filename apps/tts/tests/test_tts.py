from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tts.builder import build_tts_message
from tts.main import PIPER_BIN, PIPER_MODEL, _sanitise, app


# ---------------------------------------------------------------------------
# builder tests
# ---------------------------------------------------------------------------


def test_build_tts_message_urgency_words() -> None:
    for urgency, expected_word in [
        ("low", "low priority"),
        ("medium", "medium priority"),
        ("high", "high priority"),
        ("urgent", "urgent"),
    ]:
        msg = build_tts_message("a-b-c-d", urgency, None, include_dtmf=False)
        assert expected_word in msg


def test_build_tts_message_spoken_id() -> None:
    msg = build_tts_message("alpha-beta-gamma-delta", "high", None, include_dtmf=False)
    assert "alpha beta gamma delta" in msg
    assert "-" not in msg.split("reference:")[1].split(".")[0]


def test_build_tts_message_location() -> None:
    msg = build_tts_message("a-b-c-d", "high", "Near stage", include_dtmf=False)
    assert "Near stage" in msg


def test_build_tts_message_dtmf_prompts() -> None:
    with_dtmf = build_tts_message("a-b-c-d", "high", None, include_dtmf=True)
    without_dtmf = build_tts_message("a-b-c-d", "high", None, include_dtmf=False)
    assert "Press 1" in with_dtmf
    assert "Press 1" not in without_dtmf


def test_sanitise_strips_disallowed_chars() -> None:
    raw = "Hello <script>alert('xss')</script> world"
    result = _sanitise(raw)
    assert "<script>" not in result
    assert "Hello" in result


def test_sanitise_truncates_at_max_len() -> None:
    long_text = "a" * 600
    result = _sanitise(long_text)
    assert len(result) == 500


# ---------------------------------------------------------------------------
# API tests (mocked Piper subprocess)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_wav() -> bytes:
    return b"RIFF" + b"\x00" * 40


@pytest_asyncio.fixture
async def client(fake_wav: bytes) -> AsyncClient:  # type: ignore[misc]
    async def fake_piper(text: str, output_path: str | None = None) -> bytes:
        if output_path:
            Path(output_path).write_bytes(fake_wav)
            return b""
        return fake_wav

    with patch("tts.main._run_piper", side_effect=fake_piper):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_synthesise_returns_wav(client: AsyncClient) -> None:
    resp = await client.post("/synthesise", json={"text": "Hello"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_synthesise_file_returns_audio_url(client: AsyncClient) -> None:
    resp = await client.post("/synthesise/file", json={"text": "Hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert "audio_url" in data
    assert data["audio_url"].startswith("/audio/")


@pytest.mark.asyncio
async def test_audio_serves_file(client: AsyncClient, fake_wav: bytes) -> None:
    resp = await client.post("/synthesise/file", json={"text": "Hello"})
    token_url: str = resp.json()["audio_url"]

    audio_resp = await client.get(token_url)
    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"] == "audio/wav"


@pytest.mark.asyncio
async def test_audio_unknown_token(client: AsyncClient) -> None:
    resp = await client.get("/audio/nonexistent-token")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_synthesise_missing_params(client: AsyncClient) -> None:
    resp = await client.post("/synthesise", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_synthesise_with_friendly_id_and_urgency(client: AsyncClient) -> None:
    resp = await client.post(
        "/synthesise",
        json={"friendly_id": "a-b-c-d", "urgency": "high", "include_dtmf": False},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_degraded_when_model_absent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["checks"]["piper_model"] == "missing"
