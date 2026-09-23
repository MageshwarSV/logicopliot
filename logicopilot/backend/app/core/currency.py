"""Live USD -> INR conversion for Spend Analytics (the real OpenAI spend figure, and the
Super Admin's manually-entered account balance, both shown in INR alongside USD).

Frankfurter (https://frankfurter.dev) - genuinely free, no API key/signup, sourced from the
European Central Bank's own daily reference rates. Stdlib urllib only, same pattern as
app/core/openai_admin.py's calls to OpenAI - not enough call volume here to justify adding a
new HTTP dependency for one GET request.
"""
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

RATE_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR"


class CurrencyRateError(Exception):
    """Frankfurter unreachable or returned something this could not parse - never raised for
    a genuinely missing rate on a valid response, which cannot happen for USD/INR (both are
    always-available currencies)."""


def fetch_usd_to_inr_rate() -> tuple[float, str]:
    """(rate, as_of_date) - 1 USD in INR, and the date that rate was published (ECB updates
    once per working day; weekends/holidays carry the last published rate forward)."""
    # Frankfurter (fronted by Cloudflare) returns 403 Forbidden for Python's default
    # urllib User-Agent ("Python-urllib/3.x") - confirmed live against production, where the
    # exact same request that works from a browser was refused until this header was added.
    req = urllib.request.Request(RATE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; LogicopilotSpendAnalytics/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise CurrencyRateError(f"Could not reach the exchange rate service: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise CurrencyRateError("Exchange rate service returned an unreadable response") from exc

    try:
        rate = float(body["rates"]["INR"])
        as_of = str(body["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CurrencyRateError(f"Exchange rate response was missing what was expected: {exc}") from exc
    return rate, as_of
