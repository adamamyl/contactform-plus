from __future__ import annotations

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    oidc_issuer: str = "http://localhost:9090"
    oidc_client_id: str = "panel"
    oidc_client_secret: str = "secret"  # noqa: S105
    jwks_uri: str | None = None  # overrides oidc_issuer + /jwks for bearer token validation
    # When the panel runs inside Docker, OIDC_ISSUER is the external hostname (unreachable
    # from within the network). Set OIDC_SERVER_METADATA_URL to an internal URL so authlib
    # can fetch the well-known config, and OIDC_AUTHORIZE_URL to the external authorize
    # endpoint so the browser redirect goes to the right place.
    oidc_server_metadata_url: str | None = None
    oidc_authorize_url: str | None = None
    base_url: str = "http://localhost:8001"
    redis_url: str = "redis://redis:6379"
    dispatcher_session_ttl_hours: int = 8
    dispatcher_session_max_devices: int = 2
    current_event_override: str | None = None
    attachment_dir: str = "/app/attachments"
    router_internal_url: str = "http://msg-router:8002"
    router_internal_secret: str = ""


def get_settings() -> Settings:
    return Settings()
