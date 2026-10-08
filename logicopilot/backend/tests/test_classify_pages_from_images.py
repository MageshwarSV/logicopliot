"""Smart-Upload-only per-page vision classification (classify_page_image /
classify_pages_from_images) - the fix for a combined/multi-page file's whole-file
classification sometimes assigning the wrong page to the wrong slot. Each page is judged
from its OWN image, independent of the others; consecutive pages landing on the same slot
are merged into one document instance; a page below the confidence threshold (or with no
real evidence) is left out entirely, falling through to the existing Unclassified Pages
tray exactly like today - no new status is introduced."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.classifier import (
    AIServiceUnavailable,
    assign_documents_detailed,
    classify_page_image,
    classify_pages_from_images,
)

CANDIDATES = [
    {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
    {"key": "pl", "name": "Packing List", "doc_type": "PackingList", "fields": []},
    {"key": "bl", "name": "BL", "doc_type": "BL", "fields": []},
]


def _settings_with_key(threshold=0.85):
    s = MagicMock()
    s.openai_api_key = "fake-key-for-test"
    s.vision_classification_confidence_threshold = threshold
    return s


def _response(key="inv", confidence=0.95, evidence="Invoice No: INV-123, Total Payable: 500"):
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = (
        f'{{"matches": [{{"key": "{key}", "confidence": {confidence}, '
        f'"evidence": "{evidence}"}}]}}'
    )
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


def _empty_response():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = '{"matches": []}'
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


# ---------------------------------------------------------------------------
# classify_page_image
# ---------------------------------------------------------------------------

def test_returns_none_when_the_image_file_does_not_exist():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_page_image(
            "scan.png", 1, Path("/fake/page_1.png"), CANDIDATES, "gpt-5-mini", None)
    assert result is None
    mock_openai_cls.assert_not_called()


def test_calls_openai_with_the_page_image_and_returns_key_and_confidence(tmp_path):
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _response(key="bl", confidence=0.97)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", None)

    assert result == {"key": "bl", "confidence": 0.97,
                      "evidence": "Invoice No: INV-123, Total Payable: 500"}
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    content = kwargs["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    text = next(part["text"] for part in content if part.get("type") == "text")
    assert "page 2" in text


def test_includes_the_previous_page_s_classification_as_context(tmp_path):
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _response(key="bl")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", prev_key="bl")

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "classified as 'BL'" in text


def test_below_threshold_confidence_is_rejected(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _response(key="inv", confidence=0.5)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key(threshold=0.85)), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result is None


def test_a_match_with_no_real_evidence_is_rejected(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _response(evidence="x")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result is None


def test_no_matching_slot_returns_none(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_empty_response()):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result is None


def test_an_api_error_raises_ai_service_unavailable(tmp_path):
    from openai import APIError

    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = MagicMock()
    client.chat.completions.create.side_effect = APIError(
        "rate limited", request=MagicMock(), body=None)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        try:
            classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
            assert False, "expected AIServiceUnavailable"
        except AIServiceUnavailable:
            pass


# ---------------------------------------------------------------------------
# classify_pages_from_images - per-page grouping
# ---------------------------------------------------------------------------

def test_consecutive_pages_with_the_same_key_merge_into_one_entry(tmp_path):
    pages = []
    for i in (1, 2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    def fake_classify(filename, page_no, image_path, candidates, model, prev_key):
        return {"key": "bl", "confidence": 0.95, "evidence": f"BL evidence page {page_no}"}

    with patch("app.core.classifier.classify_page_image", side_effect=fake_classify):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert result == [{"key": "bl", "pages": [1, 2], "evidence": "BL evidence page 1",
                       "source": "model"}]


def test_a_key_change_starts_a_new_entry(tmp_path):
    pages = []
    for i in (1, 2, 3):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))
    by_page = {1: "bl", 2: "bl", 3: "inv"}

    def fake_classify(filename, page_no, image_path, candidates, model, prev_key):
        return {"key": by_page[page_no], "confidence": 0.95, "evidence": f"evidence {page_no}"}

    with patch("app.core.classifier.classify_page_image", side_effect=fake_classify):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert [(m["key"], m["pages"]) for m in result] == [("bl", [1, 2]), ("inv", [3])]


def test_an_unmatched_page_between_two_matches_breaks_the_group(tmp_path):
    pages = []
    for i in (1, 2, 3):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    def fake_classify(filename, page_no, image_path, candidates, model, prev_key):
        if page_no == 2:
            return None
        return {"key": "bl", "confidence": 0.95, "evidence": f"evidence {page_no}"}

    with patch("app.core.classifier.classify_page_image", side_effect=fake_classify):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert [(m["key"], m["pages"]) for m in result] == [("bl", [1]), ("bl", [3])]


def test_no_pages_matching_returns_empty_list(tmp_path):
    p = tmp_path / "page_1.png"
    p.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.classify_page_image", return_value=None):
        result = classify_pages_from_images("file.pdf", [(1, p)], CANDIDATES, "gpt-5-mini")
    assert result == []


def test_previous_key_is_threaded_through_between_calls(tmp_path):
    pages = []
    for i in (1, 2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))
    seen_prev_keys = []

    def fake_classify(filename, page_no, image_path, candidates, model, prev_key):
        seen_prev_keys.append(prev_key)
        return {"key": "bl", "confidence": 0.95, "evidence": f"evidence {page_no}"}

    with patch("app.core.classifier.classify_page_image", side_effect=fake_classify):
        classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert seen_prev_keys == [None, "bl"]


# ---------------------------------------------------------------------------
# assign_documents_detailed's per_page_vision routing
# ---------------------------------------------------------------------------

def _file(name, text="", image=None, page_images=None):
    return {"name": name, "text": text, "image": image, "page_images": page_images or []}


def test_per_page_vision_false_keeps_the_page_1_only_reader():
    with patch("app.core.classifier.classify_document_from_image", return_value=[]) as mock_page1, \
         patch("app.core.classifier.classify_pages_from_images") as mock_per_page:
        assign_documents_detailed(
            [_file("a.pdf")], CANDIDATES, engine="gpt5_mini_vision", vision_model="gpt-5-mini",
        )
    mock_page1.assert_called_once()
    mock_per_page.assert_not_called()


def test_per_page_vision_true_uses_the_per_page_reader(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.classify_document_from_image") as mock_page1, \
         patch("app.core.classifier.classify_pages_from_images", return_value=[]) as mock_per_page:
        assign_documents_detailed(
            [_file("a.pdf", page_images=[(1, img)])], CANDIDATES,
            engine="gpt5_mini_vision", vision_model="gpt-5-mini", per_page_vision=True,
        )
    mock_page1.assert_not_called()
    mock_per_page.assert_called_once()
    assert mock_per_page.call_args.args[-1] == "gpt-5-mini"


def test_per_page_vision_is_ignored_under_the_default_engine():
    with patch("app.core.classifier.classify_document", return_value=[]) as mock_text, \
         patch("app.core.classifier.classify_pages_from_images") as mock_per_page:
        assign_documents_detailed(
            [_file("a.pdf", text="some text")], CANDIDATES, per_page_vision=True,
        )
    mock_text.assert_called_once()
    mock_per_page.assert_not_called()
