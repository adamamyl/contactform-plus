from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import StrEnum

from .config import AppConfig, EventConfig


class Phase(StrEnum):
    PRE_EVENT = "pre_event"
    EVENT_TIME = "event_time"
    POST_EVENT = "post_event"


def current_phase(config: AppConfig, at: datetime | None = None) -> Phase:
    today: date = (at or datetime.now(tz=timezone.utc)).date()

    for event in sorted(config.events, key=lambda e: e.start_date, reverse=True):
        if event.start_date <= today <= event.end_date:
            return Phase.EVENT_TIME
        if today < event.start_date:
            return Phase.PRE_EVENT
        if today > event.end_date:
            return Phase.POST_EVENT

    return Phase.PRE_EVENT


def is_active_routing_window(config: AppConfig, at: datetime | None = None) -> bool:
    today: date = (at or datetime.now(tz=timezone.utc)).date()

    for event in config.events:
        padding = event.signal_padding
        window_start = event.start_date - timedelta(days=padding.before_event_days)
        window_end = event.end_date + timedelta(days=padding.after_event_days)
        if window_start <= today <= window_end:
            return True
    return False


def events_for_form(config: AppConfig) -> list[EventConfig]:
    return sorted(config.events, key=lambda e: e.start_date, reverse=True)
