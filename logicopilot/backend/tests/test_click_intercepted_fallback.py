"""A click the browser hands to a container is sent to the element itself (2026-11-27).

After Live Impex's screen update, Playwright reported "<div id="Panel16" class="GridPanel">
intercepts pointer events" on the Entity tab's "New" link and the Name search button - every
Air and Sea Import entry stopped there. The page's own hit-test said nothing covered them, so
_click_through went straight to a coordinate click and timed out.
"""
import pytest

from app.core import browser


class FakeLoc:
    def __init__(self, error: str | None):
        self.error = error
        self.dispatched = False
        self.first = self

    def click(self, **_kw):
        if self.error:
            raise RuntimeError(self.error)

    def evaluate(self, js):
        assert "dispatchEvent" in js
        self.dispatched = True


class FakePage:
    def __init__(self):
        self.keys = []
        self.keyboard = self

    def press(self, key):
        self.keys.append(key)


@pytest.fixture(autouse=True)
def nothing_covers(monkeypatch):
    monkeypatch.setattr(browser, "_wait_clickable", lambda *a, **k: None)
    monkeypatch.setattr(browser, "_import_rejection", lambda page: None)


def test_intercepted_click_is_sent_to_the_element():
    loc = FakeLoc('Locator.click: Timeout 8000ms exceeded.\n  - <div id="Panel16" class="GridPanel">…</div> intercepts pointer events')
    page = FakePage()
    browser._click_through(page, loc, "#lnkAdd")
    assert loc.dispatched
    assert page.keys == []            # no Escape: it could cancel the form the click opened


def test_a_normal_click_is_untouched():
    loc = FakeLoc(None)
    browser._click_through(FakePage(), loc, "#btnLogin")
    assert not loc.dispatched


def test_any_other_click_failure_still_fails():
    loc = FakeLoc("Locator.click: Timeout 8000ms exceeded.\n  - element is not visible")
    with pytest.raises(RuntimeError):
        browser._click_through(FakePage(), loc, "#missing")
    assert not loc.dispatched


def test_never_clicks_past_an_invalid_data_found_popup(monkeypatch):
    """After the ERP update the popup no longer blocks the screen behind it - a click sent
    through would carry the entry on behind a rejection (GENERAL CountryOfOriginCode)."""
    monkeypatch.setattr(browser, "_import_rejection",
                        lambda page: "GENERAL CountryOfOriginCode Entry should be of 2 characters")
    loc = FakeLoc('Locator.click: Timeout 8000ms exceeded.\n  - <div id="blueFilterDivLayer"></div> intercepts pointer events')
    with pytest.raises(RuntimeError, match="Invalid Data Found"):
        browser._click_through(FakePage(), loc, "#lnkUpdate")
    assert not loc.dispatched
