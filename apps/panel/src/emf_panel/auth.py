from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request, status

from .settings import Settings

oauth: OAuth = OAuth()


def configure_oauth(settings: Settings) -> None:
    oauth.register(
        name="emf",
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile groups"},
    )


async def require_conduct_team(request: Request) -> dict[str, object]:
    user: dict[str, object] | None = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    groups: list[str] = user.get("groups", [])  # type: ignore[assignment]
    if "team_conduct" not in groups:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
