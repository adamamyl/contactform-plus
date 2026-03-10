from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt

ALGORITHM = "HS256"
ACK_TOKEN_TTL_HOURS = 72


def create_ack_token(notification_id: uuid.UUID, secret: str) -> str:
    exp = datetime.now(tz=UTC) + timedelta(hours=ACK_TOKEN_TTL_HOURS)
    payload = {
        "sub": str(notification_id),
        "exp": exp,
        "iat": datetime.now(tz=UTC),
    }
    return str(jwt.encode(payload, secret, algorithm=ALGORITHM))


def decode_ack_token(token: str, secret: str) -> uuid.UUID:
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    return uuid.UUID(str(payload["sub"]))
