"""create_chat_completion_with_retry: every extraction call funnels through here, so a fixed
seed added once here makes the identical request reproducible everywhere, without touching
each individual prompt. Found live: the same email, pulled into two separate jobs, got two
different answers for which company was the consignee - two independent calls with no seed,
free to each pick differently."""
from unittest.mock import MagicMock, patch

from app.core.llm import create_chat_completion_with_retry


def _fake_client(capture: dict):
    client = MagicMock()

    def _create(**kwargs):
        capture.update(kwargs)
        return "ok"

    client.chat.completions.create.side_effect = _create
    return client


def test_a_default_seed_is_added_for_an_ordinary_model():
    capture = {}
    client = _fake_client(capture)
    create_chat_completion_with_retry(client=client, model="gpt-4o-mini", temperature=0,
                                      messages=[{"role": "user", "content": "x"}])
    assert "seed" in capture
    assert isinstance(capture["seed"], int)


def test_a_caller_supplied_seed_is_never_overridden():
    capture = {}
    client = _fake_client(capture)
    create_chat_completion_with_retry(client=client, model="gpt-4o-mini", seed=12345,
                                      messages=[{"role": "user", "content": "x"}])
    assert capture["seed"] == 12345


def test_no_seed_is_added_for_a_reasoning_family_model():
    # Unconfirmed whether gpt-5*/o1/o3/o4 accept this parameter at all - left alone rather
    # than guessed at, same caution already applied to temperature/max_tokens for this family.
    capture = {}
    client = _fake_client(capture)
    create_chat_completion_with_retry(client=client, model="gpt-5-mini",
                                      messages=[{"role": "user", "content": "x"}])
    assert "seed" not in capture
