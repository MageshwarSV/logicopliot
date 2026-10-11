"""normalize_place_name_with_ai (app/core/llm.py) - the pure-translation first step of a
multi-key AI-assisted lookup (see test_lookup_reference_ai_assisted.py). No candidate list
involved: just the model's own world knowledge of what a possibly old/alternate name is
called today."""
from unittest.mock import MagicMock, patch

from app.core.llm import normalize_place_name_with_ai


def _settings_with_key():
    s = MagicMock()
    s.openai_api_key = "fake-key-for-test"
    s.openai_model = "gpt-4o-mini"
    return s


def _response(name: str):
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = f'{{"name": "{name}"}}'
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


def test_returns_the_models_standard_name():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_response("Beijing")):
        assert normalize_place_name_with_ai("Peking") == "Beijing"


def test_returns_empty_when_the_model_has_nothing_to_offer():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_response("")):
        assert normalize_place_name_with_ai("Shanghai") == ""


def test_returns_empty_with_a_blank_name():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_cls:
        assert normalize_place_name_with_ai("") == ""
        assert normalize_place_name_with_ai("   ") == ""
    mock_cls.assert_not_called()


def test_returns_empty_with_no_api_key_configured():
    s = _settings_with_key()
    s.openai_api_key = ""
    with patch("app.core.llm.get_settings", return_value=s), \
         patch("openai.OpenAI") as mock_cls:
        assert normalize_place_name_with_ai("Peking") == ""
    mock_cls.assert_not_called()


def test_returns_empty_when_the_api_call_raises():
    with patch("app.core.llm.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", side_effect=RuntimeError("network down")):
        assert normalize_place_name_with_ai("Peking") == ""
