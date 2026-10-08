"""The optional `model` parameter added to all four extraction.py readers for the
vision-engine toggle. Purely additive: every existing caller (including the Template
Wizard's demo_extract/test-extract endpoints) omits it and must see zero behavior change -
proven here by confirming the default falls back to settings.openai_model exactly as before.
A caller that DOES pass it must have that model reach both the API call itself and the
reasoning-safe parameter translation (temperature/max_tokens)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.extraction import (
    extract_document_fields,
    extract_document_fields_from_images,
    extract_document_rows,
    extract_document_rows_from_images,
)

_FIELDS = [{"label": "invoice_number", "prompt": "the invoice number", "variations": []}]


def _fake_fields_client():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"invoice_number": "INV123"}'))]
    )
    return client


def _fake_rows_client():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"rows": [{"invoice_number": "INV123"}]}'))]
    )
    return client


def _settings():
    return SimpleNamespace(openai_api_key="test-key", openai_model="gpt-4o-mini")


def test_extract_document_fields_defaults_to_the_configured_model_when_omitted():
    client = _fake_fields_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("INVOICE NO: INV123", _FIELDS)
    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o-mini"
    assert client.chat.completions.create.call_args.kwargs["temperature"] == 0
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 800


def test_extract_document_fields_respects_an_override_model():
    client = _fake_fields_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("INVOICE NO: INV123", _FIELDS, model="gpt-5-mini")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    # Reasoning-safe translation must have applied: no temperature, renamed+enlarged budget.
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == 800 * 6


def test_extract_document_rows_defaults_unchanged_when_model_omitted():
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("INVOICE NO: INV123", _FIELDS)
    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o-mini"
    assert client.chat.completions.create.call_args.kwargs["temperature"] == 0


def test_extract_document_rows_respects_an_override_model():
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("INVOICE NO: INV123", _FIELDS, model="gpt-5-mini")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == 3000 * 6


def test_fields_from_images_defaults_unchanged_when_model_omitted(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_fields_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields_from_images([img], _FIELDS)
    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o-mini"
    assert client.chat.completions.create.call_args.kwargs["temperature"] == 0


def test_fields_from_images_respects_an_override_model(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_fields_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields_from_images([img], _FIELDS, model="gpt-5-mini")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == 900 * 6


def test_rows_from_images_defaults_unchanged_when_model_omitted(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows_from_images([img], _FIELDS)
    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o-mini"
    assert client.chat.completions.create.call_args.kwargs["temperature"] == 0


def test_rows_from_images_respects_an_override_model(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows_from_images([img], _FIELDS, model="gpt-5-mini")
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    assert "temperature" not in kwargs
    assert kwargs["max_completion_tokens"] == 3000 * 6
