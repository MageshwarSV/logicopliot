"""General, field-agnostic rules added to the SHARED single-value extraction prompt
(app/core/extraction.py) after two real misreads: a Bill of Lading's Sea Waybill Number field
returning the adjacent Booking Number instead (OCR had separated two captions from their two
values), and a container number picking up an extra leading digit from nearby count/size text.
Fixed at the shared-prompt level, not on one tenant's one FieldMark, so any future field on
any future document gets the same protection - not just the field these were first found on."""
from unittest.mock import MagicMock, patch

from app.core.extraction import (
    extract_document_fields,
    extract_document_fields_from_images,
    extract_document_rows,
    extract_document_rows_from_images,
)

_FIELDS = [{"label": "sea_waybill_number", "prompt": "the sea waybill number", "variations": []}]


def _fake_openai_client():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"sea_waybill_number": "WXYZ7654321"}'))]
    )
    return client


def _fake_rows_client():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"rows": [{"sea_waybill_number": "WXYZ7654321"}]}'))]
    )
    return client


def _settings():
    from types import SimpleNamespace
    return SimpleNamespace(openai_api_key="test-key", openai_model="gpt-4o-mini")


def test_text_reader_gets_the_separated_labels_and_values_rule():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("BOOKING NUMBER SEA WAYBILL NUMBER ABCD1234567 WXYZ7654321", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "PAIR THEM BY POSITION" in system_content
    assert "MATCH IT EXACTLY" in system_content


def test_text_reader_gets_the_unit_pairing_rule():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields(
            "GROSS WEIGHT (KGS) MEASUREMENT (CBM) 3244 39.6 KGS CBM", _FIELDS,
        )

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "PAIR THE NTH NUMBER WITH THE NTH UNIT" in system_content.upper()
    assert "CBM" in system_content and "KGS" in system_content


def test_vision_reader_gets_the_format_rule_but_not_the_ocr_specific_ones():
    """Vision reads the page image directly - there is no OCR-flattened label/value gap to
    correct for, so this rule would be irrelevant noise there. The format-discipline rule
    (container numbers, etc.) still applies - a misread can glue an extra digit on either way."""
    from pathlib import Path
    from unittest.mock import mock_open

    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"):
        extract_document_fields_from_images([Path("page_1.png")], _FIELDS)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "MATCH IT EXACTLY" in text_block
    assert "PAIR THEM BY POSITION" not in text_block
    assert "NTH UNIT" not in text_block.upper()


# A voyage number 'O45E' (letter O) came back as '045E' (digit 0), and a date '6/12/2026'
# (6 December, day-first) came back as '12-Jun-2026' (read as 12 June, wrong order AND wrong
# output format) - both despite one field's own prompt text already warning against exactly
# this. Promoted to shared rules every text AND vision field gets, plus every row reader,
# since neither error is specific to OCR-flattened tables - both can happen reading an image
# just as easily, and both can land on a per-row field (a line item's own date or code) too.
def test_text_reader_gets_the_letters_and_dates_rules():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("VOYAGE O45E   DATE 6/12/2026", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "LETTER O IS NOT THE DIGIT 0" in system_content.upper()
    assert "DAY FIRST" in system_content.upper()
    assert "2026-12-06" in system_content


def test_vision_reader_gets_the_letters_and_dates_rules_too():
    from pathlib import Path

    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"):
        extract_document_fields_from_images([Path("page_1.png")], _FIELDS)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "LETTER O IS NOT THE DIGIT 0" in text_block.upper()
    assert "DAY FIRST" in text_block.upper()


def test_row_reader_gets_the_letters_and_dates_rules():
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("VOYAGE O45E   DATE 6/12/2026", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "LETTER O IS NOT THE DIGIT 0" in system_content.upper()
    assert "DAY FIRST" in system_content.upper()


def test_vision_row_reader_gets_the_letters_and_dates_rules():
    from pathlib import Path

    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"):
        extract_document_rows_from_images([Path("page_1.png")], _FIELDS)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "LETTER O IS NOT THE DIGIT 0" in text_block.upper()
    assert "DAY FIRST" in text_block.upper()


def test_text_reader_gets_the_twin_blocks_rule():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields(
            "SELLER ACME CORP 100 MAIN ST BOSTON MA SHIP FROM GLOBAL FREIGHT LTD 5 DOCK RD "
            "ROTTERDAM", _FIELDS,
        )

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "ROW-BAND BY ROW-BAND ACROSS TWO COLUMNS" in system_content.upper()
    assert "SHIP FROM" in system_content


def test_row_reader_gets_the_twin_blocks_rule():
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("SELLER ACME CORP SHIP FROM GLOBAL FREIGHT LTD", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "ROW-BAND BY ROW-BAND ACROSS TWO COLUMNS" in system_content.upper()


# A run of Air Import jobs whose only attached document was the clearing agent's own
# 'CheckList - BILL OF ENTRY' summary (not a real air waybill or invoice) came back with every
# field null, because every prompt above is written for the ORIGINAL document's own layout.
def test_text_reader_gets_the_checklist_substitute_rule():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("CheckList - BILL OF ENTRY FOR WAREHOUSING", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "CLEARING/CUSTOMS AGENT'S OWN SUMMARY" in system_content.upper()
    assert "Importer Detail" in system_content


def test_vision_reader_gets_the_checklist_substitute_rule():
    from pathlib import Path

    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"):
        extract_document_fields_from_images([Path("page_1.png")], _FIELDS)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "CLEARING/CUSTOMS AGENT'S OWN SUMMARY" in text_block.upper()


def test_row_reader_gets_the_checklist_substitute_rule():
    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_rows("CheckList - BILL OF ENTRY FOR WAREHOUSING", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "ITEM DETAILS" in system_content


def test_vision_row_reader_gets_the_checklist_substitute_rule():
    from pathlib import Path

    client = _fake_rows_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client), \
         patch.object(Path, "read_bytes", return_value=b"fake-png-bytes"):
        extract_document_rows_from_images([Path("page_1.png")], _FIELDS)

    content_blocks = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    text_block = next(b["text"] for b in content_blocks if b["type"] == "text")
    assert "ITEM DETAILS" in text_block


def test_text_reader_gets_the_short_code_format_rule():
    client = _fake_openai_client()
    with patch("app.core.extraction.get_settings", return_value=_settings()), \
         patch("openai.OpenAI", return_value=client):
        extract_document_fields("PORT OF LOADING: HONG KONG", _FIELDS)

    system_content = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "SHORTER THAN ITS STATED LENGTH" in system_content.upper()
    assert "HKHKG" in system_content

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert isinstance(messages[1]["content"], str)
    assert "ATTACHED, EXACTLY AS PRINTED" not in messages[0]["content"]
