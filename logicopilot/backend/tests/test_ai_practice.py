"""Practice AI: locked off without PRACTICE_AI_ALLOWED, record/replay round trip, and replay
never reaches OpenAI."""
from types import SimpleNamespace

import pytest

from app.core import ai_practice, llm


def test_off_without_the_env_flag(monkeypatch):
    monkeypatch.delenv("PRACTICE_AI_ALLOWED", raising=False)
    assert not ai_practice.allowed()
    assert ai_practice.current_mode() == "off"


def test_request_key_ignores_seed_but_not_content():
    a = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "x"}]}
    assert ai_practice.request_key(a) == ai_practice.request_key({**a, "seed": 7})
    assert ai_practice.request_key(a) != ai_practice.request_key({**a, "messages": [{"role": "user", "content": "y"}]})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_practice, "_path", lambda key: tmp_path / key[:2] / f"{key}.json")
    monkeypatch.setenv("PRACTICE_AI_MISS_SECONDS", "0")
    return tmp_path


def test_record_then_replay(store):
    kwargs = {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "classify"}]}
    real = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"slot": "invoice"}'),
                                                    finish_reason="stop")])
    ai_practice.save(kwargs, real, 0.0)
    out = ai_practice.replay({**kwargs, "seed": 7})
    assert out.choices[0].message.content == '{"slot": "invoice"}'


def test_replay_of_an_unrecorded_request_is_empty(store):
    out = ai_practice.replay({"model": "m", "messages": []})
    assert out.choices[0].message.content == "{}"


def test_replay_mode_never_calls_openai(store, monkeypatch):
    monkeypatch.setattr(ai_practice, "current_mode", lambda: "replay")
    import app.core.openai_pool as pool

    def boom(**_):
        raise AssertionError("OpenAI must not be called in replay mode")

    monkeypatch.setattr(pool, "get_openai_client", boom)
    out = llm.create_chat_completion_with_retry(model="gpt-5-mini", messages=[{"role": "user", "content": "x"}])
    assert out.choices[0].message.content == "{}"
