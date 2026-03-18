from __future__ import annotations

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    oidc_issuer: str = "http://localhost:9090"
    oidc_client_id: str = "panel"
    oidc_client_secret: str = "secret"  # noqa: S105
    jwks_uri: str | None = None  # overrides oidc_issuer + /jwks for bearer token validation
    base_url: str = "http://localhost:8001"
    redis_url: str = "redis://redis:6379"
    dispatcher_session_ttl_hours: int = 8
    current_event_override: str | None = None
    attachment_dir: str = "/app/attachments"
    router_internal_url: str = "http://msg-router:8002"
    router_internal_secret: str = ""


def get_settings() -> Settings:
    return Settings()
