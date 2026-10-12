"""create_chat_completion_with_retry's NEW pool-aware path (app/core/llm.py): when called
with no `client` of its own (every real call site, after this session's refactor - see
app/core/openai_pool.py), a key that is exhausted on its own retry budget is put on cooldown
and the whole call retried fresh on the next pooled key, instead of raising immediately.
A caller that still passes its own `client` (back-compat) is untouched - see
test_llm_retry.py for that path's own coverage, unaffected by any of this."""

from unittest.mock import MagicMock, patch

from openai import AuthenticationError, RateLimitError

from app.core.llm import create_chat_completion_with_retry
from app.core.openai_pool import add_api_key


def _rate_limit_error(message: str, code: str = "rate_limit_exceeded") -> RateLimitError:
    class _FakeResponse:
        status_code = 429
        headers: dict = {}
        request = None

    return RateLimitError(message, response=_FakeResponse(), body={"code": code})


def _auth_error() -> AuthenticationError:
    import httpx

    resp = httpx.Response(401, request=httpx.Request("GET", "https://api.openai.com/v1/models"))
    return AuthenticationError("Incorrect API key provided", response=resp, body=None)


def _ok_openai_mock():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    mock_cls.return_value.models.list.return_value = MagicMock()
    return patcher, mock_cls


def _add_two_keys(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        a = add_api_key(db_session, "A", "sk-proj-a-aaaaaaaaaaaaaaaaaaaaa")
        b = add_api_key(db_session, "B", "sk-proj-b-aaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    return a, b


def test_with_no_pool_configured_behaves_exactly_like_the_single_key_path(monkeypatch):
    """No client passed, no pool added - get_pool_size() is 1, so this degrades to one
    pass through the SAME retry loop test_llm_retry.py already covers."""
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    attempts = {"n": 0}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    attempts["n"] += 1
                    raise _rate_limit_error("no credits remaining", code="credit_balance_exhausted")

    with patch("openai.OpenAI", return_value=_Client()):
        try:
            create_chat_completion_with_retry(model="gpt-4o-mini", timeout=10)
            assert False, "expected the error to propagate"
        except RateLimitError:
            pass
    assert attempts["n"] == 1


def test_a_key_exhausted_on_quota_fails_over_to_the_next_pooled_key(db_session, monkeypatch):
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    a, b = _add_two_keys(db_session)

    calls_by_key: dict[str, int] = {}

    def _fake_openai_ctor(api_key, timeout):
        client = MagicMock()

        def _create(**kwargs):
            calls_by_key[api_key] = calls_by_key.get(api_key, 0) + 1
            if "sk-proj-a" in api_key:
                raise _rate_limit_error("no credits remaining", code="credit_balance_exhausted")
            return "ok-from-b"

        client.chat.completions.create.side_effect = _create
        return client

    with patch("openai.OpenAI", side_effect=_fake_openai_ctor):
        result = create_chat_completion_with_retry(model="gpt-4o-mini", timeout=10)

    assert result == "ok-from-b"
    assert sum(calls_by_key.values()) == 2  # one failed attempt on A, one success on B


def test_a_revoked_key_fails_over_too(db_session, monkeypatch):
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    a, b = _add_two_keys(db_session)

    def _fake_openai_ctor(api_key, timeout):
        client = MagicMock()
        if "sk-proj-a" in api_key:
            client.chat.completions.create.side_effect = _auth_error()
        else:
            client.chat.completions.create.return_value = "ok-from-b"
        return client

    with patch("openai.OpenAI", side_effect=_fake_openai_ctor):
        result = create_chat_completion_with_retry(model="gpt-4o-mini", timeout=10)
    assert result == "ok-from-b"


def test_raises_only_once_every_pooled_key_has_failed(db_session, monkeypatch):
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    _add_two_keys(db_session)

    def _fake_openai_ctor(api_key, timeout):
        client = MagicMock()
        client.chat.completions.create.side_effect = _rate_limit_error(
            "no credits remaining", code="credit_balance_exhausted"
        )
        return client

    with patch("openai.OpenAI", side_effect=_fake_openai_ctor):
        try:
            create_chat_completion_with_retry(model="gpt-4o-mini", timeout=10)
            assert False, "expected the error to propagate once every key failed"
        except RateLimitError:
            pass


def test_a_caller_supplied_client_is_never_pool_aware(db_session, monkeypatch):
    """Back-compat: passing `client` explicitly means own_client=False, so this never
    touches the pool at all, even if one is configured - exactly today's behavior."""
    monkeypatch.setattr("app.core.llm.time.sleep", lambda s: None)
    _add_two_keys(db_session)
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
        assert False, "expected the error to propagate - no pool fallback for a given client"
    except RateLimitError:
        pass
    assert attempts["n"] == 1
