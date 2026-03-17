"""
Browser-level tests verifying every form field is stored in the database.

Each parametrized case fills the real browser form, submits it, intercepts the
/api/submit JSON response to retrieve the case_id, then queries Postgres directly
to assert the stored values match what was typed.

The map-pin location (lat/lon) is simulated by firing the postMessage that the
emf-map iframe would emit, since the real map may not be reachable in the e2e
network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page, Route, expect

from conftest import SyncDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WHAT_HAPPENED = "Something happened at the event. This is the detailed description for testing."


def _fill_text(page: Page, selector: str, value: str | None) -> None:
    if value is None:
        return
    loc = page.locator(selector)
    if loc.count() and loc.is_visible():
        loc.fill(value)


def _select(page: Page, selector: str, value: str) -> None:
    loc = page.locator(selector)
    if loc.count() and loc.is_visible():
        loc.select_option(value)


def _simulate_map_pin(page: Page, lat: float, lon: float) -> None:
    """Fire the postMessage that the emf-map iframe emits on click."""
    page.evaluate(
        """([lat, lon]) => {
            window.dispatchEvent(new MessageEvent('message', {
                data: {type: 'emf-marker', lat: lat, lon: lon},
                origin: window.location.origin,
            }));
        }""",
        [lat, lon],
    )


def _submit_and_capture(page: Page, base_url: str) -> dict[str, Any]:
    """Click submit, wait for success redirect, return the parsed API response."""
    result: dict[str, Any] = {}

    def handle_route(route: Route) -> None:
        resp = route.fetch()
        try:
            result.update(resp.json())
        except Exception:
            pass
        route.fulfill(response=resp)

    page.route("**/api/submit", handle_route)
    try:
        page.locator("#submit-btn").click()
        page.wait_for_url(f"{base_url}/success**", timeout=15_000)
    finally:
        page.unroute("**/api/submit", handle_route)
    return result


# ---------------------------------------------------------------------------
# Parametrized cases
# ---------------------------------------------------------------------------

_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "minimal_required_only",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "14:00",
            "can_contact": "true",
        },
    ),
    (
        "all_reporter_fields",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "09:30",
            "reporter_name": "Test Person",
            "reporter_pronouns": "they/them",
            "reporter_email": "test@example.com",
            "reporter_phone": "07700 900123",
            "reporter_camping_with": "Workshop crew",
            "can_contact": "true",
        },
    ),
    (
        "location_text_only",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "18:00",
            "location_text": "Near the main stage entrance",
            "can_contact": "false",
        },
    ),
    (
        "location_map_pin_only",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "20:00",
            "location_lat": 52.0413,
            "location_lon": -2.3779,
            "can_contact": "true",
        },
    ),
    (
        "location_text_and_pin",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "22:00",
            "location_text": "Workshop tent B",
            "location_lat": 52.0418,
            "location_lon": -2.3772,
            "can_contact": "true",
        },
    ),
    (
        "urgency_low",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "10:00",
            "urgency": "low",
            "can_contact": "true",
        },
    ),
    (
        "urgency_high",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "11:00",
            "urgency": "high",
            "can_contact": "true",
        },
    ),
    (
        "urgency_urgent",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "12:00",
            "urgency": "urgent",
            "can_contact": "true",
        },
    ),
    (
        "all_optional_narrative_fields",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "15:00",
            "additional_info": "Additional context here.",
            "support_needed": "A quiet space to talk.",
            "outcome_hoped": "An apology would be appreciated.",
            "others_involved": "Person X and Person Y",
            "why_it_happened": "Alcohol may have been a factor.",
            "anything_else": "Nothing further to add.",
            "can_contact": "false",
        },
    ),
    (
        "can_contact_false",
        {
            "what_happened": _WHAT_HAPPENED,
            "incident_date": "2024-06-01",
            "incident_time": "16:00",
            "can_contact": "false",
        },
    ),
    (
        "kitchen_sink",
        {
            "what_happened": _WHAT_HAPPENED + " Extended detail.",
            "incident_date": "2024-06-02",
            "incident_time": "13:37",
            "reporter_name": "Full Kitchen",
            "reporter_pronouns": "she/her",
            "reporter_email": "kitchen@example.com",
            "reporter_phone": "+44 7700 900456",
            "reporter_camping_with": "The cool kids",
            "location_text": "Café area",
            "location_lat": 52.0407,
            "location_lon": -2.3768,
            "urgency": "medium",
            "additional_info": "More info.",
            "support_needed": "Mediation.",
            "outcome_hoped": "Policy change.",
            "others_involved": "Z, W",
            "why_it_happened": "Unknown.",
            "anything_else": "That's all.",
            "can_contact": "true",
        },
    ),
]


# ---------------------------------------------------------------------------
# Cross-product: every urgency × every event
# ---------------------------------------------------------------------------

_URGENCIES = ["low", "medium", "high", "urgent"]

_EVENTS = [
    "EMF 2026",
    "EMF 2024",
    "EMF 2022",
    "EMF 2018",
    "EMF 2016",
    "EMF 2014",
    "EMF 2012",
]

_URGENCY_EVENT_CASES = [
    (f"{urgency}_x_{event.replace(' ', '_')}", urgency, event)
    for urgency in _URGENCIES
    for event in _EVENTS
]


@pytest.mark.e2e
@pytest.mark.parametrize(
    "case_name,urgency,event_name",
    _URGENCY_EVENT_CASES,
    ids=[c[0] for c in _URGENCY_EVENT_CASES],
)
def test_urgency_and_event_stored(
    page: Page,
    form_base_url: str,
    db: SyncDB,
    case_name: str,
    urgency: str,
    event_name: str,
) -> None:
    page.goto(form_base_url)
    expect(page.locator("#conduct-form")).to_be_visible()

    page.locator("#what_happened").fill(_WHAT_HAPPENED)
    page.locator("#incident_date").fill("2024-06-01")
    page.locator("#incident_time").fill("12:00")
    page.locator("#event_name").select_option(event_name)
    _select(page, "#urgency", urgency)
    page.locator("input[name=can_contact][value=true]").check()

    api_resp = _submit_and_capture(page, form_base_url)
    assert "case_id" in api_resp, f"No case_id in response: {api_resp}"
    case_id = api_resp["case_id"]

    row = db.fetchrow(
        "SELECT urgency, event_name FROM forms.cases WHERE id = $1::uuid",
        case_id,
    )
    assert row is not None, f"Case {case_id} not found in DB"
    assert row["urgency"] == urgency, f"urgency: {row['urgency']!r} != {urgency!r}"
    assert row["event_name"] == event_name, f"event_name: {row['event_name']!r} != {event_name!r}"


# ---------------------------------------------------------------------------
# The main field-storage test
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.parametrize("case_name,fields", _CASES, ids=[c[0] for c in _CASES])
def test_form_fields_stored_in_db(
    page: Page,
    form_base_url: str,
    db: SyncDB,
    case_name: str,
    fields: dict[str, Any],
) -> None:
    page.goto(form_base_url)
    expect(page.locator("#conduct-form")).to_be_visible()

    # --- what_happened (required) ---
    page.locator("#what_happened").fill(fields["what_happened"])

    # --- incident date / time ---
    page.locator("#incident_date").fill(fields["incident_date"])
    page.locator("#incident_time").fill(fields["incident_time"])

    # --- reporter section ---
    _fill_text(page, "#reporter_name", fields.get("reporter_name"))
    _fill_text(page, "#reporter_pronouns", fields.get("reporter_pronouns"))
    _fill_text(page, "#reporter_email", fields.get("reporter_email"))
    _fill_text(page, "#reporter_phone", fields.get("reporter_phone"))
    _fill_text(page, "#reporter_camping_with", fields.get("reporter_camping_with"))

    # --- location text ---
    _fill_text(page, "#location_text", fields.get("location_text"))

    # --- map pin (simulated postMessage) ---
    if "location_lat" in fields and "location_lon" in fields:
        _simulate_map_pin(page, fields["location_lat"], fields["location_lon"])
        # confirm the hidden inputs were populated
        lat_val = page.locator("#location_lat").input_value()
        assert lat_val, "Map pin postMessage did not populate location_lat"

    # --- urgency (only visible when LOCAL_DEV=true) ---
    if "urgency" in fields:
        _select(page, "#urgency", fields["urgency"])

    # --- narrative fields ---
    _fill_text(page, "#additional_info", fields.get("additional_info"))
    _fill_text(page, "#support_needed", fields.get("support_needed"))
    _fill_text(page, "#outcome_hoped", fields.get("outcome_hoped"))
    _fill_text(page, "#others_involved", fields.get("others_involved"))
    _fill_text(page, "#why_it_happened", fields.get("why_it_happened"))
    _fill_text(page, "#anything_else", fields.get("anything_else"))

    # --- can_contact (required radio) ---
    page.locator(f"input[name=can_contact][value={fields['can_contact']}]").check()

    # --- submit and capture case_id ---
    api_resp = _submit_and_capture(page, form_base_url)
    assert "case_id" in api_resp, f"No case_id in response: {api_resp}"
    case_id = api_resp["case_id"]

    # --- query DB ---
    row = db.fetchrow(
        "SELECT urgency, form_data, location_hint FROM forms.cases WHERE id = $1::uuid",
        case_id,
    )
    assert row is not None, f"Case {case_id} not found in DB"

    form_data: dict[str, Any] = json.loads(row["form_data"])

    # what_happened
    assert form_data["what_happened"] == fields["what_happened"].strip()

    # incident date / time
    assert form_data["incident_date"] == fields["incident_date"]
    assert form_data["incident_time"].startswith(fields["incident_time"])

    # reporter fields
    reporter = form_data.get("reporter", {})
    for key in ("name", "pronouns", "email", "camping_with"):
        field_key = f"reporter_{key}"
        if field_key in fields:
            assert (
                reporter.get(key) == fields[field_key]
            ), f"reporter.{key} mismatch: {reporter.get(key)!r} != {fields[field_key]!r}"
    if "reporter_phone" in fields:
        assert (
            reporter.get("phone") == fields["reporter_phone"].strip().upper()
        ), f"reporter.phone mismatch: {reporter.get('phone')!r}"

    # location
    loc = form_data.get("location")
    if "location_text" in fields:
        assert (
            row["location_hint"] == fields["location_text"].strip()
        ), f"location_hint mismatch: {row['location_hint']!r}"
        assert loc is not None and loc.get("text") == fields["location_text"].strip()
    if "location_lat" in fields:
        assert loc is not None
        assert (
            abs(loc["lat"] - fields["location_lat"]) < 1e-6
        ), f"location.lat mismatch: {loc['lat']!r} != {fields['location_lat']!r}"
        assert (
            abs(loc["lon"] - fields["location_lon"]) < 1e-6
        ), f"location.lon mismatch: {loc['lon']!r} != {fields['location_lon']!r}"
    if "location_text" not in fields and "location_lat" not in fields:
        assert loc is None, f"Expected no location, got: {loc}"

    # urgency
    expected_urgency = fields.get("urgency", "medium")
    assert (
        row["urgency"] == expected_urgency
    ), f"urgency mismatch: {row['urgency']!r} != {expected_urgency!r}"

    # narrative optional fields
    for key in (
        "additional_info",
        "support_needed",
        "outcome_hoped",
        "others_involved",
        "why_it_happened",
        "anything_else",
    ):
        expected = fields.get(key)
        stored = form_data.get(key)
        assert stored == expected, f"{key} mismatch: {stored!r} != {expected!r}"

    # can_contact
    expected_contact = fields["can_contact"] == "true"
    assert (
        form_data.get("can_contact") == expected_contact
    ), f"can_contact mismatch: {form_data.get('can_contact')!r} != {expected_contact!r}"
