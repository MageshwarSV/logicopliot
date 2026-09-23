"""app/core/openai_admin.py — every failure calling OpenAI's real API must come back as ONE
clear OpenAIAdminAPIError, never a raw/unhandled exception that would surface as an opaque
502 or 500 with no explanation of what actually went wrong."""
import json
import urllib.error
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.core.openai_admin import OpenAIAdminAPIError, fetch_daily_costs, fetch_daily_token_usage


def _fake_response(body: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_fetch_daily_costs_sums_results_per_day():
    body = {
        "data": [
            {"start_time": 1757203200, "results": [{"amount": {"value": 1.5}}, {"amount": {"value": 0.5}}]},
        ],
        "has_more": False,
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        result = fetch_daily_costs("sk-admin", date(2025, 9, 7), date(2025, 9, 7))
    assert result == {"2025-09-07": 2.0}


def test_fetch_daily_token_usage_sums_input_and_output():
    body = {
        "data": [
            {"start_time": 1757203200, "results": [
                {"input_tokens": 100, "output_tokens": 10},
                {"input_tokens": 50, "output_tokens": 5},
            ]},
        ],
        "has_more": False,
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        result = fetch_daily_token_usage("sk-admin", date(2025, 9, 7), date(2025, 9, 7))
    assert result == {"2025-09-07": {"input_tokens": 150, "output_tokens": 15}}


def test_http_error_wrapped_with_status_and_body():
    err = urllib.error.HTTPError(
        url="https://api.openai.com/v1/organization/costs", code=401, msg="Unauthorized",
        hdrs=None, fp=MagicMock(read=lambda: b'{"error": "invalid api key"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(OpenAIAdminAPIError) as exc_info:
            fetch_daily_costs("sk-bad", date(2026, 9, 7), date(2026, 9, 7))
    assert "401" in str(exc_info.value)
    assert "invalid api key" in str(exc_info.value)


def test_unparseable_json_wrapped_not_raw_crash():
    mock = MagicMock()
    mock.read.return_value = b"<html>not json</html>"
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=mock):
        with pytest.raises(OpenAIAdminAPIError) as exc_info:
            fetch_daily_costs("sk-admin", date(2026, 9, 7), date(2026, 9, 7))
    assert "unparseable" in str(exc_info.value).lower()


def test_unexpected_shape_wrapped_not_raw_crash():
    """A bucket missing "start_time" (OpenAI changes a field, a partial response) must not
    surface as an unhandled KeyError — it should read as one clear OpenAIAdminAPIError."""
    body = {"data": [{"results": [{"amount": {"value": 1.0}}]}], "has_more": False}
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        with pytest.raises(OpenAIAdminAPIError) as exc_info:
            fetch_daily_costs("sk-admin", date(2026, 9, 7), date(2026, 9, 7))
    assert "cost response" in str(exc_info.value).lower()


def test_network_unreachable_wrapped():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(OpenAIAdminAPIError) as exc_info:
            fetch_daily_costs("sk-admin", date(2026, 9, 7), date(2026, 9, 7))
    assert "could not reach openai" in str(exc_info.value).lower()


def test_request_uses_the_limit_openai_actually_allows():
    """OpenAI rejects bucket_width="1d" requests with limit above 31 outright - "Limit
    exceeds the maximum allowed value of 31 for the given bucket_width" - confirmed against
    a real Admin key. Every request this module sends must ask for at most 31."""
    body = {"data": [], "has_more": False}
    captured_urls = []

    def _urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return _fake_response(body)

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        fetch_daily_costs("sk-admin", date(2026, 8, 1), date(2026, 9, 10))
    assert captured_urls, "no request was made"
    for url in captured_urls:
        assert "limit=31" in url
        assert "limit=180" not in url
