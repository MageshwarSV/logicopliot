"""expected_row_count on extract_document_rows/extract_document_rows_from_images
(app/core/extraction.py) - the prompt-side half of the Document AI row-count cross-check (see
test_row_total_cross_check.py for the jobs.py/run_extraction side). No existing extraction
test patches the OpenAI client directly (every one mocks at the app.api.v1.jobs call boundary
instead) - this is the one place that actually needs to inspect the prompt text itself, so it
patches openai.OpenAI, the same class extract_document_rows imports locally."""
import json
from unittest.mock import MagicMock, patch

from app.core.extraction import extract_document_rows, extract_document_rows_from_images

_FIELDS = [{"label": "item_quantity", "prompt": "the quantity", "variations": []}]


def _fake_openai_client(rows=()):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"rows": list(rows)})))]
    )
    return client


def _settings():
    from types import SimpleNamespace
    return SimpleNamespace(openai_api_key="test-key", openai_model="gpt-4o-mini")


def test_omitting_expected_row_count_leaves_the_prompt_unchanged():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("some OCR text", _FIELDS)

    user_content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "independently detected" not in user_content


def test_expected_row_count_is_named_in_the_prompt():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("some OCR text", _FIELDS, expected_row_count=12)

    user_content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "exactly 12 data row(s)" in user_content
    assert "Return exactly 12 row-object(s)" in user_content


def test_zero_expected_row_count_is_treated_as_no_signal():
    """0 means Document AI found no table at all (see docai.py) - never "the table has zero
    rows", so it must produce exactly the same prompt as omitting the argument."""
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("some OCR text", _FIELDS, expected_row_count=0)

    user_content = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "independently detected" not in user_content


def test_vision_variant_also_carries_the_hint():
    from pathlib import Path
    from unittest.mock import mock_open

    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"), \
         patch.object(Path, "exists", return_value=True):
        extract_document_rows_from_images([Path("page_1.png")], _FIELDS, expected_row_count=5)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "exactly 5 data row(s)" in text_block
