from __future__ import annotations

URGENCY_WORDS: dict[str, str] = {
    "low": "low priority",
    "medium": "medium priority",
    "high": "high priority",
    "urgent": "urgent",
}

DTMF_PROMPTS = (
    "Press 1 to acknowledge this case. Press 2 to pass to the next responder."
)


def _friendly_id_spoken(friendly_id: str) -> str:
    return friendly_id.replace("-", " ")


def build_tts_message(
    friendly_id: str,
    urgency: str,
    location_hint: str | None,
    include_dtmf: bool = True,
) -> str:
    urgency_word = URGENCY_WORDS.get(urgency, urgency)
    spoken_id = _friendly_id_spoken(friendly_id)
    location_part = f"Location: {location_hint}. " if location_hint else ""
    dtmf_part = DTMF_PROMPTS if include_dtmf else ""
    return (
        f"New {urgency_word} conduct case. "
        f"Case reference: {spoken_id}. "
        f"{location_part}"
        f"{dtmf_part}"
    ).strip()
