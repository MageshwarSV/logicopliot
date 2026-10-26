"""Smart-Upload-only per-page vision classification (classify_page_image /
classify_pages_from_images) - the fix for a combined/multi-page file's whole-file
classification sometimes assigning the wrong page to the wrong slot. Each page is judged
from its OWN image, independent of the others; consecutive pages landing on the same slot
are merged into one document instance; a page below the confidence threshold (or with no
real evidence) is left out entirely, falling through to the existing Unclassified Pages
tray exactly like today - no new status is introduced.

classify_page_image returns a LIST of matches, not a single best guess: a page can
genuinely, physically combine more than one document type at once (an Invoice/Packing-List-
in-one page), and classify_pages_from_images tracks each key as its own independently
open/closed group so a combined page lands under every key it matches."""
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


def _response(matches):
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    parts = ",".join(
        f'{{"key": "{m[0]}", "confidence": {m[1]}, "evidence": "{m[2]}"}}' for m in matches
    )
    fake_response.choices[0].message.content = f'{{"matches": [{parts}]}}'
    client = MagicMock()
    client.chat.completions.create.return_value = fake_response
    return client


def _single_response(key="inv", confidence=0.95, evidence="Invoice No: INV-123, Total Payable: 500"):
    return _response([(key, confidence, evidence)])


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

def test_returns_empty_list_when_the_image_file_does_not_exist():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_page_image(
            "scan.png", 1, Path("/fake/page_1.png"), CANDIDATES, "gpt-5-mini", None)
    assert result == []
    mock_openai_cls.assert_not_called()


def test_calls_openai_with_the_page_image_and_returns_key_and_confidence(tmp_path):
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="bl", confidence=0.97)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", None)

    assert result == [{"key": "bl", "confidence": 0.97,
                       "evidence": "Invoice No: INV-123, Total Payable: 500"}]
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5-mini"
    content = kwargs["messages"][0]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    text = next(part["text"] for part in content if part.get("type") == "text")
    assert "page 2" in text


def test_a_genuinely_combined_page_returns_both_matches(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _response([
        ("inv", 0.9, "Invoice No and unit prices present"),
        ("pl", 0.88, "Per-package weight breakdown also on this same page"),
    ])
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("combined.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)

    assert {m["key"] for m in result} == {"inv", "pl"}


def test_the_prompt_includes_a_typical_content_hint_per_candidate(tmp_path):
    # Not a heuristic rule for telling documents apart - just what the candidate's own name
    # even refers to, reusing the same DOC_TYPE_HINTS/_hint_from_name lookup
    # classify_document already relies on. Without it, a real Arrival Notice confidently
    # says "none of these 4 types fit" when the slot is only known by an opaque custom name
    # like "Fright Certificate" - this is the fix for exactly that regression.
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="bl")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "typically:" in text
    assert "bill of lading" in text.lower()


def test_a_custom_typed_slot_still_gets_a_hint_from_its_own_name(tmp_path):
    # A tenant's own slot, typed "Custom" rather than one of the 4 built-ins, spelled however
    # they spelled it ("Fright Certificate") - _hint_from_name's own job.
    custom_candidates = [
        {"key": "fright", "name": "Fright Certificate", "doc_type": "Custom", "fields": []},
    ]
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="fright")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 1, img, custom_candidates, "gpt-5-mini", None)

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "freight charges" in text.lower()


def test_a_bl_candidate_gets_the_arrival_notice_disambiguation_warning(tmp_path):
    # Found live: a forwarder's Arrival Notice (shipper/consignee/vessel/port fields, same as
    # a real BL) got misread as a Bill of Lading by the per-page vision engine - it has no
    # anti-confusion guidance at all unlike the older text classifier's own prompt. This one
    # targeted warning must be present whenever a BL-typed slot is among the candidates.
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="bl")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "ARRIVAL NOTICE" in text
    assert "NOT a Bill of Lading" in text


def test_the_arrival_notice_warning_is_omitted_when_no_bl_candidate_exists(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    no_bl_candidates = [c for c in CANDIDATES if c["key"] != "bl"]
    client = _single_response(key="inv")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 1, img, no_bl_candidates, "gpt-5-mini", None)

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "ARRIVAL NOTICE" not in text


def test_includes_the_previous_page_s_classification_as_context(tmp_path):
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="bl")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", prev_keys=["bl"])

    text = next(
        part["text"] for part in client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        if part.get("type") == "text"
    )
    assert "classified as:" in text and "'BL'" in text


def test_below_threshold_confidence_is_rejected(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="inv", confidence=0.5)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key(threshold=0.85)), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result == []


def test_a_continuation_match_is_trusted_even_at_zero_confidence(tmp_path):
    # A carrier's own continuation/attachment page rarely looks like a Bill of Lading by
    # itself - no Shipper/Consignee box, no title - so the model can honestly self-rate near
    # zero even while correctly agreeing this page continues an already-open document.
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="bl", confidence=0.0, evidence="Marks and numbers continued from page 1")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key(threshold=0.85)), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", prev_keys=["bl"])
    assert result == [{"key": "bl", "confidence": 0.0,
                       "evidence": "Marks and numbers continued from page 1"}]


def test_a_fresh_match_not_in_prev_keys_still_needs_real_confidence(tmp_path):
    # The continuation exception only applies to a key that was already open - a different
    # key offered alongside a continuation hint is a fresh claim, not a continuation, and
    # must still clear the confidence bar like any other fresh match.
    img = tmp_path / "page_2.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(key="inv", confidence=0.5)
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key(threshold=0.85)), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 2, img, CANDIDATES, "gpt-5-mini", prev_keys=["bl"])
    assert result == []


def test_a_match_with_no_real_evidence_is_rejected(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    client = _single_response(evidence="x")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=client):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result == []


def test_no_matching_slot_returns_empty_list(tmp_path):
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=_empty_response()):
        result = classify_page_image("file.pdf", 1, img, CANDIDATES, "gpt-5-mini", None)
    assert result == []


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

def _fake_classify(by_page):
    def fake(filename, page_no, image_path, candidates, model, prev_keys, **_kwargs):
        keys = by_page.get(page_no, [])
        return [{"key": k, "confidence": 0.95, "evidence": f"evidence {page_no} {k}"} for k in keys]
    return fake


def test_consecutive_pages_with_the_same_key_merge_into_one_entry(tmp_path):
    pages = []
    for i in (1, 2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    with patch("app.core.classifier.classify_page_image",
              side_effect=_fake_classify({1: ["bl"], 2: ["bl"]})):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert result == [{"key": "bl", "pages": [1, 2], "evidence": "evidence 1 bl",
                       "source": "model"}]


def test_a_key_change_starts_a_new_entry(tmp_path):
    pages = []
    for i in (1, 2, 3):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    with patch("app.core.classifier.classify_page_image",
              side_effect=_fake_classify({1: ["bl"], 2: ["bl"], 3: ["inv"]})):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert [(m["key"], m["pages"]) for m in result] == [("bl", [1, 2]), ("inv", [3])]


def test_an_unmatched_page_between_two_matches_breaks_the_group(tmp_path):
    pages = []
    for i in (1, 2, 3):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    with patch("app.core.classifier.classify_page_image",
              side_effect=_fake_classify({1: ["bl"], 2: [], 3: ["bl"]})):
        result = classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert [(m["key"], m["pages"]) for m in result] == [("bl", [1]), ("bl", [3])]


def test_no_pages_matching_returns_empty_list(tmp_path):
    p = tmp_path / "page_1.png"
    p.write_bytes(b"\x89PNG\r\n")
    with patch("app.core.classifier.classify_page_image", return_value=[]):
        result = classify_pages_from_images("file.pdf", [(1, p)], CANDIDATES, "gpt-5-mini")
    assert result == []


def test_previous_keys_are_threaded_through_between_calls(tmp_path):
    pages = []
    for i in (1, 2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))
    seen_prev_keys = []

    def fake_classify(filename, page_no, image_path, candidates, model, prev_keys, **_kwargs):
        seen_prev_keys.append(list(prev_keys))
        return [{"key": "bl", "confidence": 0.95, "evidence": f"evidence {page_no}"}]

    with patch("app.core.classifier.classify_page_image", side_effect=fake_classify):
        classify_pages_from_images("file.pdf", pages, CANDIDATES, "gpt-5-mini")

    assert seen_prev_keys == [[], ["bl"]]


def test_a_combined_page_opens_two_groups_that_can_close_independently(tmp_path):
    # Page 1 is genuinely BOTH an invoice and a packing list; page 2 continues only the
    # packing list (e.g. a second page of per-package rows) - the invoice group must close
    # after page 1 while the packing-list group keeps extending.
    pages = []
    for i in (1, 2):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        pages.append((i, p))

    with patch("app.core.classifier.classify_page_image",
              side_effect=_fake_classify({1: ["inv", "pl"], 2: ["pl"]})):
        result = classify_pages_from_images("combined.pdf", pages, CANDIDATES, "gpt-5-mini")

    by_key = {m["key"]: m["pages"] for m in result}
    assert by_key == {"inv": [1], "pl": [1, 2]}


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
