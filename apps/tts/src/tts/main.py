from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
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
PIPER_MODEL = os.environ.get("PIPER_MODEL", "/app/models/en_GB-alan-medium.onnx")
PIPER_BIN = os.environ.get("PIPER_BIN", "/app/piper/piper")
AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", "/app/audio"))

# token → (file_path, created_at, persistent)
# persistent=True means the file should not be deleted on expiry
_audio_files: dict[str, tuple[str, float, bool]] = {}

_ALLOWED_CHARS = re.compile(r"[^\w\s\.,\-'!?:;/()]")


def _sanitise(text: str) -> str:
    text = _ALLOWED_CHARS.sub("", text)
    return text[:MAX_TEXT_LEN]


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [
        t
        for t, (_, created, persistent) in _audio_files.items()
        if not persistent and now - created > AUDIO_TTL_SECONDS
    ]
    for t in expired:
        path, _, _ = _audio_files.pop(t)
        try:
            os.unlink(path)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    yield
    for path, _, persistent in _audio_files.values():
        if not persistent:
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

    cache_key = hashlib.sha256(text.encode()).hexdigest()
    cache_path = AUDIO_DIR / f"{cache_key}.wav"

    if not cache_path.exists():
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        await _run_piper(text, output_path=str(cache_path))

    token = secrets.token_urlsafe(16)
    _audio_files[token] = (str(cache_path), time.monotonic(), True)

    return JSONResponse({"audio_url": f"/audio/{token}"})


@app.get("/audio/{token}")
async def serve_audio(token: str) -> FileResponse:
    _purge_expired()
    entry = _audio_files.get(token)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    path, _, _ = entry
    if not Path(path).exists():
        _audio_files.pop(token, None)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path, media_type="audio/wav")


@app.get("/health", tags=["ops"])
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
