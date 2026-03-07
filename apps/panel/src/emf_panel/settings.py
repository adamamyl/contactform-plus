from __future__ import annotations

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    oidc_issuer: str = "http://localhost:9090"
    oidc_client_id: str = "panel"
    oidc_client_secret: str = "secret"
    base_url: str = "http://localhost:8001"
    redis_url: str = "redis://redis:6379"
    dispatcher_session_ttl_hours: int = 8
    current_event_override: str | None = None
    attachment_dir: str = "/app/attachments"


def get_settings() -> Settings:
    return Settings()
