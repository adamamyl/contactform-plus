from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    local_dev: bool = False
    attachment_dir: Path = Path("/app/attachments")
    google_safe_browsing_key: str = ""
    redis_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
