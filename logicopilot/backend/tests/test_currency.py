"""app/core/currency.py — live USD -> INR rate for Spend Analytics. Every failure reaching
Frankfurter must come back as ONE clear CurrencyRateError, matching the same convention
already used for OpenAI's own Admin API calls (see test_openai_admin_module.py)."""
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from app.core.currency import CurrencyRateError, fetch_usd_to_inr_rate


def _fake_response(body: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_fetch_rate_returns_rate_and_date():
    body = {"amount": 1.0, "base": "USD", "date": "2026-09-15", "rates": {"INR": 95.96}}
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        rate, as_of = fetch_usd_to_inr_rate()
    assert rate == 95.96
    assert as_of == "2026-09-15"


def test_network_error_wrapped_as_currency_rate_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(CurrencyRateError):
            fetch_usd_to_inr_rate()


def test_unparseable_json_wrapped_as_currency_rate_error():
    mock = MagicMock()
    mock.read.return_value = b"not json"
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=mock):
        with pytest.raises(CurrencyRateError):
            fetch_usd_to_inr_rate()


def test_missing_rate_in_response_wrapped_as_currency_rate_error():
    body = {"amount": 1.0, "base": "USD", "date": "2026-09-15", "rates": {}}
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        with pytest.raises(CurrencyRateError):
            fetch_usd_to_inr_rate()
