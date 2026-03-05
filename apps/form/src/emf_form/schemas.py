from __future__ import annotations

import re
from datetime import date, datetime, time, timezone

from pydantic import BaseModel, EmailStr, field_validator, model_validator

_URGENCY_VALUES = {"low", "medium", "high", "urgent"}
_PHONE_RE = re.compile(r"^[\d\s+\-.()\sA-Z]+$")


class Location(BaseModel):
    text: str | None = None
    lat: float | None = None
    lon: float | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> Location:
        if self.text is None and self.lat is None and self.lon is None:
            raise ValueError("At least one of text, lat, or lon must be provided")
        return self

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v: float | None) -> float | None:
        if v is not None and not (-90.0 <= v <= 90.0):
            raise ValueError("lat must be between -90 and 90")
        return v

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, v: float | None) -> float | None:
        if v is not None and not (-180.0 <= v <= 180.0):
            raise ValueError("lon must be between -180 and 180")
        return v

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) > 500:
                raise ValueError("text must be at most 500 characters")
            return v if v else None
        return v


class ReporterDetails(BaseModel):
    name: str | None = None
    pronouns: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    camping_with: str | None = None

    @field_validator("name", "pronouns", "camping_with", mode="before")
    @classmethod
    def strip_str(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("phone", mode="before")
    @classmethod
    def normalise_phone(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip().upper()
            if not v:
                return None
            if not _PHONE_RE.match(v):
                raise ValueError(
                    "Phone number contains invalid characters. "
                    "Allowed: digits, spaces, +, -, ., (, ), A-Z"
                )
            return v
        return v


class CaseSubmission(BaseModel):
    event_name: str
    reporter: ReporterDetails
    what_happened: str
    incident_date: date
    incident_time: time
    location: Location | None = None
    additional_info: str | None = None
    support_needed: str | None = None
    outcome_hoped: str | None = None
    urgency: str = "medium"
    others_involved: str | None = None
    why_it_happened: str | None = None
    can_contact: bool
    anything_else: str | None = None
    website: str | None = None

    @field_validator(
        "event_name",
        "additional_info",
        "support_needed",
        "outcome_hoped",
        "others_involved",
        "why_it_happened",
        "anything_else",
        mode="before",
    )
    @classmethod
    def strip_text_fields(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v

    @field_validator("what_happened", mode="before")
    @classmethod
    def strip_what_happened(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("what_happened")
    @classmethod
    def validate_what_happened(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("what_happened must be at least 10 characters")
        if len(v) > 10000:
            raise ValueError("what_happened must be at most 10000 characters")
        return v

    @field_validator("incident_date")
    @classmethod
    def not_in_future(cls, v: date) -> date:
        today = datetime.now(tz=timezone.utc).date()
        if v > today:
            raise ValueError("incident date cannot be in the future")
        return v

    @field_validator("urgency")
    @classmethod
    def validate_urgency(cls, v: str) -> str:
        if v not in _URGENCY_VALUES:
            raise ValueError(f"urgency must be one of {sorted(_URGENCY_VALUES)}")
        return v

    @field_validator("website")
    @classmethod
    def honeypot_must_be_empty(cls, v: str | None) -> str | None:
        return v
