"""_safe_wait/_safe_close - several real ERPs (SOFTLINK among them) close or navigate their
own tab the instant a submission finishes. A plain page.wait_for_timeout()/ctx.close() on a
page/browser the SITE ITSELF already closed raised uncaught, which escaped all the way to
play_steps' own outer except and reported a flat hard "error" - discarding a run that had
already reached the real finish line (screenshot, captured data and all) moments earlier.
These two helpers exist so that never happens again; this is a focused unit test of them in
isolation, since mocking a full Playwright browser/context/page through play_steps itself
would test far more than what actually changed."""
from unittest.mock import MagicMock

from app.core.browser import _safe_close, _safe_wait


def test_safe_wait_swallows_closed_page_error():
    page = MagicMock()
    page.wait_for_timeout.side_effect = Exception(
        "Target page, context or browser has been closed"
    )
    _safe_wait(page, 3000)  # must not raise
    page.wait_for_timeout.assert_called_once_with(3000)


def test_safe_wait_calls_through_on_a_live_page():
    page = MagicMock()
    _safe_wait(page, 500)
    page.wait_for_timeout.assert_called_once_with(500)


def test_safe_close_swallows_errors_on_both_ctx_and_browser():
    ctx = MagicMock()
    browser = MagicMock()
    ctx.close.side_effect = Exception("already closed")
    browser.close.side_effect = Exception("already closed")
    _safe_close(ctx, browser)  # must not raise
    ctx.close.assert_called_once()
    browser.close.assert_called_once()


def test_safe_close_still_closes_browser_even_if_ctx_close_fails():
    """A failure closing the context must not skip closing the browser too - otherwise the
    very error this exists to tolerate would leak a live browser process instead."""
    ctx = MagicMock()
    browser = MagicMock()
    ctx.close.side_effect = Exception("boom")
    _safe_close(ctx, browser)
    browser.close.assert_called_once()


def test_safe_close_calls_through_on_live_objects():
    ctx = MagicMock()
    browser = MagicMock()
    _safe_close(ctx, browser)
    ctx.close.assert_called_once()
    browser.close.assert_called_once()
