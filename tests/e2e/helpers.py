from __future__ import annotations

from datetime import date


def make_valid_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_name": "EMF 2026",
        "reporter": {
            "name": "E2E Test Person",
            "pronouns": "They/Them",
            "email": None,
            "phone": None,
            "camping_with": None,
        },
        "what_happened": "Something happened at the event during end-to-end testing.",
        "incident_date": str(date(2024, 5, 30)),
        "incident_time": "14:00:00",
        "location": {"text": "Main stage area"},
        "urgency": "medium",
        "additional_info": None,
        "support_needed": None,
        "outcome_hoped": None,
        "others_involved": None,
        "why_it_happened": None,
        "can_contact": True,
        "anything_else": None,
        "website": None,
    }
    base.update(overrides)
    return base
