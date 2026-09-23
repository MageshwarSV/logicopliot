"""Real spend and token usage from OpenAI's own organization-level Admin API — the only
source of the ACTUAL billed dollar figure, as opposed to app/api/v1/system_settings.py's
character-count ESTIMATE. Requires a separate "Admin API key" (Organization > Admin keys on
platform.openai.com) — the regular project API key used to make calls has no permission to
read what those calls cost, by OpenAI's own design, so this is deliberately a second
credential rather than reusing the one already configured.

Uses the stdlib only (urllib) rather than adding a new dependency — this is two GET calls,
not enough to justify pulling in `requests` for a project that does not otherwise use it.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openai.com/v1"
# Bucketed daily; a hard cap on pages so a malformed/unexpected response can never spin
# forever — OpenAI's own limit is 31 daily buckets per page (see _paged_buckets), so the
# widest range this UI offers (90 days) takes 3 pages, comfortably inside this cap.
_MAX_PAGES = 10


class OpenAIAdminAPIError(Exception):
    """The Admin API rejected the request — bad key, no read-usage permission, or OpenAI
    itself is unreachable. Never raised for "no data in range" — that is a normal, empty
    result, not an error."""


def _get(path: str, api_key: str, params: dict) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}{path}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OpenAIAdminAPIError(f"OpenAI Admin API returned {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise OpenAIAdminAPIError(f"Could not reach OpenAI: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # A non-JSON 200 (a proxy's error page, a truncated body) must not surface as a
        # generic 500 with no explanation - the whole point of OpenAIAdminAPIError is that
        # every failure calling OpenAI, including a malformed response, gets one clear
        # message the endpoint can turn into a real 502 instead of crashing.
        raise OpenAIAdminAPIError(
            f"OpenAI Admin API returned unparseable JSON: {raw[:300]!r}") from exc


def _day_range_to_unix(start: date, end: date) -> tuple[int, int]:
    """end is inclusive on the way in (matches the rest of this app's date handling) — OpenAI's
    end_time is exclusive, so it is bumped one day here rather than by every caller."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _bucket_day(start_time: int) -> str:
    return datetime.fromtimestamp(start_time, tz=timezone.utc).date().isoformat()


def _paged_buckets(path: str, api_key: str, start: date, end: date) -> list[dict]:
    start_ts, end_ts = _day_range_to_unix(start, end)
    buckets: list[dict] = []
    page = None
    for _ in range(_MAX_PAGES):
        # OpenAI's own cap for bucket_width="1d" is 31, not the 180 this used to send - every
        # call with a real Admin key failed outright with "Limit exceeds the maximum allowed
        # value of 31 for the given bucket_width" before a single bucket ever came back. 31
        # buckets is also exactly enough for one calendar month in one request; wider ranges
        # still page correctly via has_more/next_page below.
        params = {"start_time": start_ts, "end_time": end_ts, "bucket_width": "1d", "limit": 31}
        if page:
            params["page"] = page
        body = _get(path, api_key, params)
        buckets.extend(body.get("data", []))
        if not body.get("has_more") or not body.get("next_page"):
            break
        page = body["next_page"]
    return buckets


def fetch_daily_costs(api_key: str, start: date, end: date) -> dict[str, float]:
    """{date: real dollars spent that day}, summed across every line item OpenAI reports."""
    out: dict[str, float] = {}
    try:
        for bucket in _paged_buckets("/organization/costs", api_key, start, end):
            day = _bucket_day(bucket["start_time"])
            total = sum((r.get("amount") or {}).get("value") or 0 for r in bucket.get("results", []))
            out[day] = out.get(day, 0.0) + total
    except OpenAIAdminAPIError:
        raise
    except Exception as exc:  # noqa: BLE001
        # A shape this parsing did not expect (OpenAI changed a field, a bucket missing
        # "start_time") must still come back as ONE clear reason, not a raw 500 with no
        # explanation of what actually went wrong reading the response.
        raise OpenAIAdminAPIError(f"Could not read OpenAI's cost response: {exc}") from exc
    return out


def fetch_daily_token_usage(api_key: str, start: date, end: date) -> dict[str, dict[str, int]]:
    """{date: {"input_tokens": int, "output_tokens": int}} — the REAL number, to compare
    against this app's own character-based estimate."""
    out: dict[str, dict[str, int]] = {}
    try:
        for bucket in _paged_buckets("/organization/usage/completions", api_key, start, end):
            day = _bucket_day(bucket["start_time"])
            bucket_totals = out.setdefault(day, {"input_tokens": 0, "output_tokens": 0})
            for r in bucket.get("results", []):
                bucket_totals["input_tokens"] += r.get("input_tokens") or 0
                bucket_totals["output_tokens"] += r.get("output_tokens") or 0
    except OpenAIAdminAPIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OpenAIAdminAPIError(f"Could not read OpenAI's usage response: {exc}") from exc
    return out
