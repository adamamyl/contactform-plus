from __future__ import annotations

from emf_shared.config import Settings as BaseSettings


class Settings(BaseSettings):
    local_dev: bool = False


def get_settings() -> Settings:
    return Settings()
