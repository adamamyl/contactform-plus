from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt
import redis.asyncio as aioredis
from fastapi import HTTPException


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


async def validate_dispatcher_token(
    token: str,
    device_id: str,
    secret_key: str,
    redis: aioredis.Redis,
    max_devices: int = 2,
) -> dict[str, object]:
    try:
        payload: dict[str, object] = jwt.decode(token, secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as err:
        raise HTTPException(status_code=401, detail="Invalid or expired session token") from err

    jti = str(payload.get("jti", ""))
    devices_key = f"dispatcher:devices:{jti}"

    # Pipeline round-trip 1: revocation check + device membership together
    pipe = redis.pipeline()
    pipe.exists(f"dispatcher:revoked:{jti}")
    pipe.sismember(devices_key, device_id)
    is_revoked, is_known = await pipe.execute()

    if is_revoked:
        raise HTTPException(status_code=401, detail="Session revoked")
    if payload.get("scope") != "dispatcher":
        raise HTTPException(status_code=403, detail="Insufficient scope")

    if not is_known:
        count = await redis.scard(devices_key)
        if count >= max_devices:
            raise HTTPException(status_code=403, detail="Maximum devices for this session reached")
        # Pipeline round-trip 2: register device + set TTL together
        exp = payload.get("exp")
        pipe2 = redis.pipeline()
        pipe2.sadd(devices_key, device_id)
        if isinstance(exp, (int, float)):
            ttl = int(exp) - int(datetime.now(tz=UTC).timestamp())
            if ttl > 0:
                pipe2.expire(devices_key, ttl)
        await pipe2.execute()

    return payload


async def revoke_token(jti: str, redis: aioredis.Redis) -> None:
    await redis.set(f"dispatcher:revoked:{jti}", "1", ex=86400)
    await redis.delete(f"dispatcher:devices:{jti}")


async def get_active_device_count(jti: str, redis: aioredis.Redis) -> int:
    count = await redis.scard(f"dispatcher:devices:{jti}")
    return int(count)
