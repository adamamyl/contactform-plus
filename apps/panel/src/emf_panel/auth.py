from __future__ import annotations

import logging
from functools import lru_cache

import jwt
from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, status
from jwt import InvalidTokenError, PyJWKClient

from .settings import Settings

log = logging.getLogger(__name__)

oauth: OAuth = OAuth()


def configure_oauth(settings: Settings) -> None:
    oauth.register(
        name="emf",
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile groups"},
    )


@lru_cache(maxsize=8)
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    return PyJWKClient(jwks_uri, cache_keys=True)


def _verify_bearer(token: str, jwks_uri: str, issuer: str) -> dict[str, object] | None:
    try:
        client = _jwks_client(jwks_uri)
        signing_key = client.get_signing_key_from_jwt(token)
        claims: dict[str, object] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
        return claims
    except (InvalidTokenError, Exception):
        log.debug("Bearer token validation failed", exc_info=True)
        return None


async def require_conduct_team(request: Request) -> dict[str, object]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from .settings import get_settings

        settings = get_settings()
        jwks_uri = settings.jwks_uri or f"{settings.oidc_issuer}/jwks"
        claims = _verify_bearer(auth_header[7:], jwks_uri, settings.oidc_issuer)
        if claims is not None:
            groups: list[str] = claims.get("groups", [])  # type: ignore[assignment]
            if "team_conduct" in groups:
                return claims
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or insufficient bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user: dict[str, object] | None = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    session_groups: list[str] = user.get("groups", [])  # type: ignore[assignment]
    if "team_conduct" not in session_groups:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
