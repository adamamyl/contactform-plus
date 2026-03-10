from datetime import UTC, datetime

from emf_shared.config import AppConfig, EventConfig, SignalPadding, SmtpConfig
from emf_shared.phase import Phase, current_phase, events_for_form, is_active_routing_window


def _make_config(
    start: str,
    end: str,
    before_pad: int = 2,
    after_pad: int = 2,
    extra: list[EventConfig] | None = None,
) -> AppConfig:
    events = [
        EventConfig(
            name="emfcamp2026",
            start_date=start,  # type: ignore[arg-type]
            end_date=end,  # type: ignore[arg-type]
            signal_padding=SignalPadding(before_event_days=before_pad, after_event_days=after_pad),
        )
    ]
    if extra:
        events.extend(extra)
    return AppConfig(
        events=events,
        conduct_emails=["conduct@emfcamp.org"],
        smtp=SmtpConfig(from_addr="conduct@emfcamp.org"),
        panel_base_url="https://panel.emfcamp.org",
    )


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


class TestCurrentPhase:
    def test_pre_event(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-06-01")) == Phase.PRE_EVENT

    def test_event_time_start(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-07-12")) == Phase.EVENT_TIME

    def test_event_time_mid(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-07-15")) == Phase.EVENT_TIME

    def test_event_time_end(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-07-20")) == Phase.EVENT_TIME

    def test_post_event(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-07-21")) == Phase.POST_EVENT

    def test_multi_event_between_events_is_pre_event(self) -> None:
        config = _make_config(
            "2026-07-12",
            "2026-07-20",
            extra=[
                EventConfig(
                    name="emfcamp2028",
                    start_date="2028-07-05",  # type: ignore[arg-type]
                    end_date="2028-07-10",  # type: ignore[arg-type]
                )
            ],
        )
        # Between two events: most-recent (2028) not started yet → PRE_EVENT
        assert current_phase(config, _at("2027-01-01")) == Phase.PRE_EVENT

    def test_post_event_after_last_event(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert current_phase(config, _at("2026-08-01")) == Phase.POST_EVENT

    def test_no_events_returns_pre_event(self) -> None:
        config = AppConfig(
            events=[],
            conduct_emails=["conduct@emfcamp.org"],
            smtp=SmtpConfig(from_addr="conduct@emfcamp.org"),
            panel_base_url="https://panel.emfcamp.org",
        )
        assert current_phase(config) == Phase.PRE_EVENT


class TestActiveRoutingWindow:
    def test_outside_padding_window(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert not is_active_routing_window(config, _at("2026-07-01"))

    def test_within_before_padding(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert is_active_routing_window(config, _at("2026-07-10"))

    def test_during_event(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert is_active_routing_window(config, _at("2026-07-15"))

    def test_within_after_padding(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert is_active_routing_window(config, _at("2026-07-22"))

    def test_outside_after_padding(self) -> None:
        config = _make_config("2026-07-12", "2026-07-20")
        assert not is_active_routing_window(config, _at("2026-07-25"))


class TestEventsForForm:
    def test_sorted_most_recent_first(self) -> None:
        config = _make_config(
            "2026-07-12",
            "2026-07-20",
            extra=[
                EventConfig(
                    name="emfcamp2028",
                    start_date="2028-07-05",  # type: ignore[arg-type]
                    end_date="2028-07-10",  # type: ignore[arg-type]
                )
            ],
        )
        result = events_for_form(config)
        assert result[0].name == "emfcamp2028"
        assert result[1].name == "emfcamp2026"
