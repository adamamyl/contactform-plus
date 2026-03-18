from __future__ import annotations

from pathlib import Path

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    local_dev: bool = False
    attachment_dir: Path = Path("/app/attachments")
    google_safe_browsing_key: str = ""


def get_settings() -> Settings:
    return Settings()
