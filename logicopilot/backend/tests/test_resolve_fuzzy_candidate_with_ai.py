"""resolve_fuzzy_candidate_with_ai (app/core/llm.py) - the AI-assisted second step of a
multi-key fuzzy lookup (see test_lookup_reference_ai_assisted.py): given a SHORT, already-
narrowed list of (name, code) candidates, decides which one a document's own printed name
refers to. The one safety property that matters: it may only ever return a code that was
actually offered to it - never invent one."""
from unittest.mock import MagicMock, patch

from app.core.llm import resolve_fuzzy_candidate_with_ai

CANDIDATES = [("Shanghai", "CNSHA"), ("Pudong/Shanghai", "CNPDG"), ("Shanghai Pt", "CNSGP")]


def _settings_with_key():
    s = MagicMock()
    s.openai_api_key = "fake-key-for-test"
    s.openai_model = "gpt-4o-mini"
    return s


def _response(code: str):
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = f'{{"code": "{code}"}}'
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


def test_returns_the_matching_candidates_code():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_response("CNSHA")):
        assert resolve_fuzzy_candidate_with_ai("Shanghai", CANDIDATES) == "CNSHA"


def test_rejects_a_code_the_model_invents_outside_the_candidate_list():
    """The one safety property that matters: whatever the model answers, it is only trusted
    if it names a code that was actually in the list offered to it."""
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_response("CNXYZ")):
        assert resolve_fuzzy_candidate_with_ai("Shanghai", CANDIDATES) == ""


def test_returns_empty_when_the_model_says_none_match():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_response("")):
        assert resolve_fuzzy_candidate_with_ai("Atlantis", CANDIDATES) == ""


def test_returns_empty_with_no_candidates():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_cls:
        assert resolve_fuzzy_candidate_with_ai("Shanghai", []) == ""
    mock_cls.assert_not_called()


def test_returns_empty_with_no_api_key_configured():
    s = _settings_with_key()
    s.openai_api_key = ""
    with patch("app.core.llm.get_settings", return_value=s), \
         patch("openai.OpenAI") as mock_cls:
        assert resolve_fuzzy_candidate_with_ai("Shanghai", CANDIDATES) == ""
    mock_cls.assert_not_called()


def test_returns_empty_when_the_api_call_raises():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", side_effect=RuntimeError("network down")):
        assert resolve_fuzzy_candidate_with_ai("Shanghai", CANDIDATES) == ""
