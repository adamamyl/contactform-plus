import pytest
from pydantic import ValidationError

from emf_shared.config import AppConfig, EventConfig, SmtpConfig


def _base_config(**kwargs: object) -> AppConfig:
    defaults: dict[str, object] = {
        "events": [
            EventConfig(
                name="emfcamp2026",
                start_date="2026-07-12",  # type: ignore[arg-type]
                end_date="2026-07-20",  # type: ignore[arg-type]
            )
        ],
        "conduct_emails": ["conduct@emfcamp.org"],
        "smtp": SmtpConfig(from_addr="conduct@emfcamp.org"),
        "panel_base_url": "https://panel.emfcamp.org",
    }
    defaults.update(kwargs)
    return AppConfig.model_validate(defaults)


def test_valid_config() -> None:
    config = _base_config()
    assert len(config.events) == 1
    assert config.events[0].name == "emfcamp2026"


def test_end_date_before_start_raises() -> None:
    with pytest.raises(ValidationError):
        EventConfig(
            name="bad",
            start_date="2026-07-20",  # type: ignore[arg-type]
            end_date="2026-07-12",  # type: ignore[arg-type]
        )


def test_missing_conduct_emails_raises() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "events": [],
                "smtp": {"from_addr": "x@x.com"},
                "panel_base_url": "https://panel.example.org",
            }
        )


def test_default_urgency_levels() -> None:
    config = _base_config()
    assert config.urgency_levels == ["low", "medium", "high", "urgent"]


def test_example_file_validates() -> None:
    import json
    from pathlib import Path

    example = Path(__file__).parent.parent.parent / "config.json-example"
    raw = json.loads(example.read_text())
    config = AppConfig.model_validate(raw)
    assert len(config.events) >= 1
