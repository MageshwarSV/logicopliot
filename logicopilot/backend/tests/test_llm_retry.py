"""create_chat_completion_with_retry / _rate_limit_wait_seconds (app/core/llm.py) - the
shared retry wrapper every OpenAI chat-completion call in this codebase goes through. Built
after a real production incident: several fields on a live job were silently written as
empty because their calls landed in a brief tokens-per-minute rate-limit window, with no
retry at all - indistinguishable in the UI from "the AI genuinely found nothing"."""
from openai import RateLimitError

from app.core.llm import _rate_limit_wait_seconds, create_chat_completion_with_retry


class _FakeResponse:
    def __init__(self, status_code: int = 429):
        self.status_code = status_code
        self.headers: dict = {}
        self.request = None


def _rate_limit_error(message: str, code: str = "rate_limit_exceeded") -> RateLimitError:
    return RateLimitError(message, response=_FakeResponse(), body={"code": code})


def test_wait_seconds_is_none_for_a_non_rate_limit_error():
    """The one 429 retrying can never fix: an exhausted prepaid balance. No wait is ever
    right for this, so the caller must raise immediately."""
    exc = _rate_limit_error("no credits remaining", code="credit_balance_exhausted")
    assert _rate_limit_wait_seconds(exc) is None


def test_wait_seconds_parses_milliseconds_from_the_real_openai_message():
    exc = _rate_limit_error(
        "Rate limit reached for gpt-4o-mini ... Please try again in 372ms. Visit ..."
    )
    assert _rate_limit_wait_seconds(exc) == 0.372


def test_wait_seconds_parses_seconds_from_the_real_openai_message():
    exc = _rate_limit_error(
        "Rate limit reached for gpt-4o-mini ... Please try again in 1.844s. Visit ..."
    )
    assert _rate_limit_wait_seconds(exc) == 1.844


def test_wait_seconds_defaults_when_rate_limited_but_no_wait_is_parseable():
    exc = _rate_limit_error("Rate limit reached for gpt-4o-mini, try again shortly.")
    assert _rate_limit_wait_seconds(exc) == 1.0


def test_succeeds_immediately_when_the_call_does_not_fail():
    calls = []

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return "ok"

    result = create_chat_completion_with_retry(_Client(), model="gpt-4o-mini")
    assert result == "ok"
    assert len(calls) == 1


def test_retries_a_genuine_rate_limit_and_then_succeeds(monkeypatch):
    """The exact real-world shape: the first call is rate-limited, the second (after
    waiting the time OpenAI itself suggested) succeeds - the caller gets the successful
    result, not an empty default."""
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: sleeps.append(s))

    attempts = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    attempts["n"] += 1
                    if attempts["n"] == 1:
                        raise _rate_limit_error(
                            "Rate limit reached ... Please try again in 372ms. Visit ..."
                        )
                    return "ok-after-retry"

    result = create_chat_completion_with_retry(_Client(), model="gpt-4o-mini")
    assert result == "ok-after-retry"
    assert attempts["n"] == 2
    assert sleeps == [0.372]


def test_does_not_retry_an_exhausted_balance(monkeypatch):
    """insufficient_quota/credit_balance_exhausted must raise on the FIRST attempt - no
    amount of waiting adds money to an empty prepaid balance."""
    sleeps: list[float] = []
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    attempts["n"] += 1
                    raise _rate_limit_error("no credits remaining", code="credit_balance_exhausted")

    try:
        create_chat_completion_with_retry(_Client(), model="gpt-4o-mini")
        assert False, "expected RateLimitError to propagate"
    except RateLimitError:
        pass
    assert attempts["n"] == 1
    assert sleeps == []


def test_gives_up_after_the_retry_budget_and_raises_the_last_error(monkeypatch):
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    attempts = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    attempts["n"] += 1
                    raise _rate_limit_error(
                        "Rate limit reached ... Please try again in 10ms. Visit ..."
                    )

    try:
        create_chat_completion_with_retry(_Client(), model="gpt-4o-mini")
        assert False, "expected RateLimitError to propagate once retries are exhausted"
    except RateLimitError:
        pass
    # _MAX_RATE_LIMIT_RETRIES retries + the original attempt.
    from app.core.llm import _MAX_RATE_LIMIT_RETRIES
    assert attempts["n"] == _MAX_RATE_LIMIT_RETRIES + 1
