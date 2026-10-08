"""classify_document_from_image - the vision-engine counterpart to classify_document, used
only when the extraction_engine system setting is "gpt5_mini_vision". Mirrors
classify_document's evidence-or-discard discipline exactly, but reads ONLY the page-1 image,
never OCR text - and must never claim any page other than 1, since page 1 is the only page
it is ever shown (the documented v1 scope limitation)."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.classifier import (
    AIServiceUnavailable,
    assign_documents_detailed,
    classify_document,
    classify_document_from_image,
)

CANDIDATES = [
    {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
    {"key": "pl", "name": "Packing List", "doc_type": "PackingList", "fields": []},
    {"key": "bl", "name": "BL", "doc_type": "BL", "fields": []},
]


def _settings_with_key():
    s = MagicMock()
    s.openai_api_key = "fake-key-for-test"
    s.openai_model = "gpt-4o-mini"
    return s


def _fake_match_response(key="inv", evidence="Invoice No: INV-123, Total Payable: 500"):
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = (
        f'{{"matches": [{{"key": "{key}", "pages": [1], "evidence": "{evidence}"}}]}}'
    )
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


def test_returns_empty_when_no_image_path_given_without_calling_openai():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_document_from_image("scan.png", None, CANDIDATES, "gpt-5-mini")
    assert result == []
    mock_openai_cls.assert_not_called()


def test_returns_empty_when_the_image_file_does_not_exist():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls, \
         patch("pathlib.Path.exists", return_value=False):
        result = classify_document_from_image(
            "scan.png", Path("/fake/page_1.png"), CANDIDATES, "gpt-5-mini")
    assert result == []
    mock_openai_cls.assert_not_called()


def test_calls_openai_with_the_given_model_and_an_image_content_block(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_match_response()
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_document_from_image("invoice.pdf", img, CANDIDATES, "gpt-5-mini")

    assert [m["key"] for m in result] == ["inv"]
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    # Reasoning-safe translation applied for a reasoning-family model override.
    assert "temperature" not in kwargs
    assert "max_completion_tokens" in kwargs
    content = kwargs["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    assert any(part.get("type") == "text" for part in content)


def test_never_claims_any_page_other_than_1_even_if_the_model_tries_to(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    # The model claims page 3 - impossible, since it was only ever shown page 1.
    fake_response.choices[0].message.content = (
        '{"matches": [{"key": "inv", "pages": [1, 2, 3], "evidence": "Invoice No: INV-123"}]}'
    )
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_document_from_image("invoice.pdf", img, CANDIDATES, "gpt-5-mini")
    assert result == [{"key": "inv", "pages": [1], "evidence": "Invoice No: INV-123",
                       "source": "model"}]


def test_a_match_with_no_real_evidence_is_dropped(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _fake_match_response(evidence="x")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_document_from_image("invoice.pdf", img, CANDIDATES, "gpt-5-mini")
    assert result == []


def test_an_api_error_raises_ai_service_unavailable_not_a_silent_empty_result(tmp_path):
    from openai import APIError

    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = MagicMock()
    client.chat.completions.create.side_effect = APIError(
        "rate limited", request=MagicMock(), body=None)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        try:
            classify_document_from_image("invoice.pdf", img, CANDIDATES, "gpt-5-mini")
            assert False, "expected AIServiceUnavailable"
        except AIServiceUnavailable:
            pass


# ---------------------------------------------------------------------------
# assign_documents_detailed's engine routing
# ---------------------------------------------------------------------------

def _file(name, text="", image=None):
    return {"name": name, "text": text, "image": image, "page_count": 1}


def test_default_engine_uses_the_text_reader_exactly_as_before():
    with patch("app.core.classifier.classify_document", return_value=[]) as mock_text, \
         patch("app.core.classifier.classify_document_from_image") as mock_vision:
        assign_documents_detailed([_file("a.pdf", text="some text")], CANDIDATES)
    mock_text.assert_called_once()
    mock_vision.assert_not_called()


def test_vision_engine_uses_the_image_reader_with_the_given_model(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.classify_document") as mock_text, \
         patch("app.core.classifier.classify_document_from_image", return_value=[]) as mock_vision:
        assign_documents_detailed(
            [_file("a.pdf", image=img)], CANDIDATES,
            engine="gpt5_mini_vision", vision_model="gpt-5-mini",
        )
    mock_text.assert_not_called()
    mock_vision.assert_called_once()
    assert mock_vision.call_args.args[-1] == "gpt-5-mini"
