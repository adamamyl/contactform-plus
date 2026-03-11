from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
import jwt

_revoked: set[str] = set()
_active_sessions: dict[str, list[str]] = {}


def create_dispatcher_token(secret_key: str, ttl_hours: int) -> str:
    jti = secrets.token_urlsafe(16)
    now = datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": "dispatcher",
        "jti": jti,
        "exp": now + timedelta(hours=ttl_hours),
        "iat": now,
        "scope": "dispatcher",
    }
    token: str = jwt.encode(payload, secret_key, algorithm="HS256")
    return token


def validate_dispatcher_token(token: str, device_id: str, secret_key: str) -> dict[str, object]:
    try:
        payload: dict[str, object] = jwt.decode(token, secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as err:
        raise HTTPException(status_code=401, detail="Invalid or expired session token") from err

    jti = str(payload.get("jti", ""))
    if jti in _revoked:
        raise HTTPException(status_code=401, detail="Session revoked")
    if payload.get("scope") != "dispatcher":
        raise HTTPException(status_code=403, detail="Insufficient scope")

    devices = _active_sessions.setdefault(jti, [])
    if device_id not in devices:
        if len(devices) >= 2:
            raise HTTPException(status_code=403, detail="Maximum devices for this session reached")
        devices.append(device_id)

    return payload


def revoke_token(jti: str) -> None:
    _revoked.add(jti)
    _active_sessions.pop(jti, None)


def get_active_device_count(jti: str) -> int:
    return len(_active_sessions.get(jti, []))
