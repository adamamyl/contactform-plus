from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings


class SignalPadding(BaseModel):
    before_event_days: int = 2
    after_event_days: int = 2


class EventConfig(BaseModel):
    name: str
    start_date: date
    end_date: date
    signal_group_id: str | None = None
    signal_mode: str = "fallback_only"
    signal_padding: SignalPadding = SignalPadding()
    jambonz_mode: str = "disabled"
    call_group_number: str | None = None
    dispatcher_emails: list[str] = []
    dispatcher_session_ttl_hours: int = 8
    dispatcher_session_max_devices: int = 2

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info: object) -> date:
        data = getattr(info, "data", {})
        if "start_date" in data and v < data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v


class SmtpConfig(BaseModel):
    host: str = "host.docker.internal"
    port: int = 587
    from_addr: str
    use_tls: bool = True
    username: str | None = None


class SiteMap(BaseModel):
    lat: float
    lon: float
    zoom: float = 16
    map_url: str = "https://map.emf-forms.internal"


class AppConfig(BaseModel):
    events: list[EventConfig]
    conduct_emails: list[str]
    urgency_levels: list[str] = ["low", "medium", "high", "urgent"]
    pronouns: list[str] = [
        "Ze/Zir/Zirs",
        "Xe/Xem/Xyrs",
        "Fae/Faer/Faerself",
        "Fur/Furs/Furself",
        "He/Him/His",
        "She/Her/Hers",
        "They/Them/Theirs",
    ]
    smtp: SmtpConfig
    site_map: SiteMap | None = None
    panel_base_url: str
    mattermost_webhook: str | None = None
    mattermost_url: str | None = None
    mattermost_channel_id: str | None = None
    slack_webhook: str | None = None
    attachment_backend: str = "local"
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_max_per_case: int = 3
    rate_limit_per_minute: int = 5
    rate_limit_per_hour: int = 20


class Settings(BaseSettings):
    database_url: str
    config_path: Path = Path("config.json")
    secret_key: str
    smtp_password: str = ""

    @property
    def app_config(self) -> AppConfig:
        return AppConfig.model_validate(json.loads(self.config_path.read_text()))

    model_config = {"env_file": ".env"}
