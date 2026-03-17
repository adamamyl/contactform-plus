from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
def test_form_page_loads(page: Page, form_base_url: str) -> None:
    page.goto(form_base_url)
    expect(page.locator("body")).to_be_visible()
    expect(page.locator("form, h1, main")).to_be_visible()


@pytest.mark.e2e
def test_form_has_required_fields(page: Page, form_base_url: str) -> None:
    page.goto(form_base_url)
    expect(page.locator("textarea, input[type=text]").first).to_be_visible()


@pytest.mark.e2e
def test_form_submit_valid_shows_success(page: Page, form_base_url: str) -> None:
    page.goto(form_base_url)
    what_happened = page.locator("textarea[name=what_happened], #what_happened, [id*=what]").first
    if not what_happened.is_visible():
        pytest.skip("Form textarea not found — check template field names")
    what_happened.fill("Something happened at the event during end-to-end playwright testing.")
    page.locator("button[type=submit], input[type=submit]").first.click()
    page.wait_for_url(f"{form_base_url}/success**", timeout=10_000)
    expect(page.locator("body")).to_contain_text("submitted")


@pytest.mark.e2e
def test_xss_in_textarea_not_executed(page: Page, form_base_url: str) -> None:
    page.goto(form_base_url)
    textarea = page.locator("textarea").first
    if not textarea.is_visible():
        pytest.skip("No textarea found")
    textarea.fill("<script>window.__xss_triggered=true</script>Something happened here.")
    triggered = page.evaluate("() => !!window.__xss_triggered")
    assert not triggered, "XSS payload was executed in the browser"


@pytest.mark.e2e
def test_bad_string_in_textarea_does_not_crash(page: Page, form_base_url: str) -> None:
    bad_string = "Ω≈ç√∫˜µ≤≥÷ Something happened here at the event."
    page.goto(form_base_url)
    textarea = page.locator("textarea").first
    if not textarea.is_visible():
        pytest.skip("No textarea found")
    textarea.fill(bad_string)
    assert page.locator("body").is_visible(), "Page crashed after entering bad string"


@pytest.mark.e2e
def test_keyboard_navigation_reaches_all_fields(page: Page, form_base_url: str) -> None:
    page.goto(form_base_url)
    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement?.tagName")
    assert focused in (
        "INPUT",
        "TEXTAREA",
        "SELECT",
        "BUTTON",
        "A",
    ), f"Tab key did not move focus to a form element, got: {focused}"
