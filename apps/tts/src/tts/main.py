from __future__ import annotations

import asyncio
import os
import re
import secrets
import tempfile
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from tts.builder import build_tts_message

MAX_TEXT_LEN = 500
AUDIO_TTL_SECONDS = 300
PIPER_MODEL = os.environ.get(
    "PIPER_MODEL", "/app/models/en_GB-alan-medium.onnx"
)
PIPER_BIN = os.environ.get("PIPER_BIN", "/app/piper/piper")

# token → (file_path, created_at)
_audio_files: dict[str, tuple[str, float]] = {}

_ALLOWED_CHARS = re.compile(r"[^\w\s\.,\-'!?:;/()]")


def _sanitise(text: str) -> str:
    text = _ALLOWED_CHARS.sub("", text)
    return text[:MAX_TEXT_LEN]


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [t for t, (_, created) in _audio_files.items() if now - created > AUDIO_TTL_SECONDS]
    for t in expired:
        path, _ = _audio_files.pop(t)
        try:
            os.unlink(path)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    for path, _ in _audio_files.values():
        try:
            os.unlink(path)
        except OSError:
            pass
    _audio_files.clear()


app = FastAPI(title="EMF TTS Service", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


class TTSRequest(BaseModel):
    text: str | None = None
    friendly_id: str | None = None
    urgency: str | None = None
    location_hint: str | None = None
    include_dtmf: bool = True


def _resolve_text(req: TTSRequest) -> str:
    if req.text:
        return _sanitise(req.text)
    if req.friendly_id and req.urgency:
        raw = build_tts_message(
            friendly_id=req.friendly_id,
            urgency=req.urgency,
            location_hint=req.location_hint,
            include_dtmf=req.include_dtmf,
        )
        return _sanitise(raw)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Provide either 'text' or 'friendly_id' + 'urgency'",
    )


async def _run_piper(text: str, output_path: str | None = None) -> bytes:
    cmd = [PIPER_BIN, "--model", PIPER_MODEL, "--output_raw"]
    if output_path:
        cmd = [PIPER_BIN, "--model", PIPER_MODEL, "--output_file", output_path]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate(input=text.encode())
    return stdout


@app.post("/synthesise")
async def synthesise_stream(req: TTSRequest) -> StreamingResponse:
    text = _resolve_text(req)
    audio = await _run_piper(text)

    async def _gen() -> AsyncGenerator[bytes, None]:
        yield audio

    return StreamingResponse(
        _gen(),
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=speech.wav"},
    )


@app.post("/synthesise/file")
async def synthesise_file(req: TTSRequest) -> JSONResponse:
    _purge_expired()
    text = _resolve_text(req)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name

    await _run_piper(text, output_path=tmp_path)

    token = secrets.token_urlsafe(16)
    _audio_files[token] = (tmp_path, time.monotonic())

    return JSONResponse({"audio_url": f"/audio/{token}"})


@app.get("/audio/{token}")
async def serve_audio(token: str) -> FileResponse:
    _purge_expired()
    entry = _audio_files.get(token)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    path, _ = entry
    if not Path(path).exists():
        _audio_files.pop(token, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path, media_type="audio/wav")


@app.get("/health")
async def health() -> dict[str, object]:
    model_ok = Path(PIPER_MODEL).exists()
    piper_ok = Path(PIPER_BIN).exists()
    status_str = "ok" if (model_ok and piper_ok) else "degraded"
    return {
        "status": status_str,
        "checks": {
            "piper_model": "ok" if model_ok else "missing",
            "piper_bin": "ok" if piper_ok else "missing",
        },
        "version": "0.1.0",
    }
