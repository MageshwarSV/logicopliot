"""Every page of an attachment is classified on its own, and a slot gets only its own pages.

JOB-00CC6D (Sea import): a 45-page "Commercial Invoice + PL" file - only its first 6 pages were
ever classified, and 36 real invoice and packing-list pages ended up Unclassified.
JOB-FAE253 (Air import): a 13-page file holding two invoices, an air waybill and a packing
list was filed WHOLE into the Invoice slot because only the invoice was recognised.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import classifier_air_import, classifier_air_export, classifier_sea_export, classifier_sea_import
from app.core.page_filter import claim_covers_file

MODULES = [classifier_sea_import, classifier_air_import, classifier_sea_export, classifier_air_export]
CANDIDATES = [
    {"key": "bl", "name": "Bill of lading", "doc_type": "BL"},
    {"key": "inv", "name": "Invoice", "doc_type": "Invoice"},
    {"key": "pl", "name": "Packing List", "doc_type": "PackingList"},
]
INVOICE_TEXT = ("COMMERCIAL INVOICE  Invoice No: 8667395  Invoice Date: 01.11.2026  "
                "Unit Price  Amount in USD  Total Invoice Value 1200.00")
PL_TEXT = ("PACKING LIST  Net Weight 120 kg  Gross Weight 140 kg  No. of packages 4  "
           "Dimension 120x80x90")
COMBINED_TEXT = INVOICE_TEXT + "  " + PL_TEXT
PLAIN_TEXT = "Thank you for your business. Page 2 of 2."


def _run(module, answers, texts, continuation=None):
    """answers: {page: key or None} for the first, context-free look. continuation: {page:
    key} returned when the page is asked again with the page before it."""
    calls = []

    def fake(filename, page_no, image_path, candidates, model, prev_key):
        calls.append((page_no, prev_key))
        key = (continuation or {}).get(page_no) if prev_key else answers.get(page_no)
        if not key:
            return None
        return {"key": key, "confidence": 0.9, "evidence": f"looks like {key} on page {page_no}"}

    images = [(n, Path(f"page_{n}.png")) for n in range(1, len(texts) + 1)]
    with patch.object(module, "classify_page_image", side_effect=fake):
        claims = module.classify_pages_from_images("file.pdf", images, CANDIDATES, "gpt-x", page_texts=texts)
    return claims, calls


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_every_page_is_judged_and_consecutive_pages_form_one_document(module):
    texts = [INVOICE_TEXT, PLAIN_TEXT, PL_TEXT, PL_TEXT, INVOICE_TEXT]
    claims, calls = _run(module, {1: "inv", 2: None, 3: "pl", 4: "pl", 5: "inv"}, texts, continuation={2: "inv"})

    assert sorted(n for n, prev in calls if prev is None) == [1, 2, 3, 4, 5]
    assert [(c["key"], c["pages"]) for c in claims] == [("inv", [1, 2]), ("pl", [3, 4]), ("inv", [5])]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_only_an_unsure_page_is_asked_again_with_the_page_before_it(module):
    texts = [PL_TEXT, INVOICE_TEXT, PLAIN_TEXT]
    # page 2 is a confident invoice: it is never told "the page before was a packing list"
    claims, calls = _run(module, {1: "pl", 2: "inv", 3: None}, texts, continuation={3: "inv"})

    assert [c for c in calls if c[1] is not None] == [(3, "inv")]
    assert [(c["key"], c["pages"]) for c in claims] == [("pl", [1]), ("inv", [2, 3])]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_a_page_the_images_could_not_place_is_placed_by_its_own_text(module):
    texts = [INVOICE_TEXT, INVOICE_TEXT, PLAIN_TEXT]
    claims, _ = _run(module, {1: None, 2: "inv", 3: None}, texts)

    assert [(c["key"], c["pages"], c["source"]) for c in claims] == [("inv", [1, 2], "model")]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_a_combined_page_gets_both_slots_but_only_that_page(module):
    # page 2 is an "invoice cum packing list": both. Page 1 and 3 are plain invoices: never PL,
    # although the FILE as a whole carries the packing-list words.
    texts = [INVOICE_TEXT, COMBINED_TEXT, INVOICE_TEXT]
    claims, _ = _run(module, {1: "inv", 2: "inv", 3: "inv"}, texts)

    got = {(c["key"], tuple(c["pages"])) for c in claims}
    assert got == {("inv", (1, 2, 3)), ("pl", (2,))}


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_a_page_nothing_recognises_is_left_unplaced(module):
    texts = [INVOICE_TEXT, PLAIN_TEXT, PLAIN_TEXT]
    claims, _ = _run(module, {1: "inv", 2: None, 3: None}, texts, continuation={})

    assert [(c["key"], c["pages"]) for c in claims] == [("inv", [1])]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_per_page_mode_does_not_spread_a_whole_file_keyword_match_over_every_page(module):
    files = [{"name": "mixed.pdf", "text": COMBINED_TEXT, "kept_pages": [INVOICE_TEXT, PL_TEXT],
              "page_images": [(1, Path("p1.png")), (2, Path("p2.png"))], "page_count": 2}]

    def fake(filename, page_no, image_path, candidates, model, prev_key):
        key = {1: "inv", 2: "pl"}[page_no]
        return {"key": key, "confidence": 0.9, "evidence": f"{key} heading"}

    with patch.object(module, "classify_page_image", side_effect=fake):
        claims = module.assign_documents_detailed(files, CANDIDATES, engine="gpt5_mini_vision",
                                                  vision_model="gpt-x", per_page_vision=True)
    assert sorted((c["key"], tuple(c["pages"])) for c in claims[0]) == [("inv", (1,)), ("pl", (2,))]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_the_ai_service_failing_on_a_page_still_stops_the_file(module):
    from app.core.classifier_common import AIServiceUnavailable

    with patch.object(module, "classify_page_image", side_effect=AIServiceUnavailable("no credits")):
        with pytest.raises(AIServiceUnavailable):
            module.classify_pages_from_images("f.pdf", [(1, Path("a.png")), (2, Path("b.png"))],
                                              CANDIDATES, "gpt-x", page_texts=["", ""])


def test_a_file_goes_whole_into_a_slot_only_when_its_claim_covers_every_page_with_text():
    pages = [INVOICE_TEXT, PLAIN_TEXT, "", PL_TEXT]
    assert claim_covers_file([1, 2, 4], pages) is True          # the blank page does not count
    assert claim_covers_file([1, 2], pages) is False            # page 4 is something else
    assert claim_covers_file([], []) is True


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.split(".")[-1])
def test_there_is_no_page_limit_a_thousand_page_file_has_every_page_classified(module):
    texts = [INVOICE_TEXT] * 1000
    claims, calls = _run(module, {n: "inv" for n in range(1, 1001)}, texts)

    assert len({n for n, prev in calls if prev is None}) == 1000
    assert [(c["key"], len(c["pages"]), c["pages"][0], c["pages"][-1]) for c in claims] == [("inv", 1000, 1, 1000)]


def test_neither_reading_path_stops_at_a_page_count():
    """The email and upload paths render and OCR every page - no "if i > N: break" left."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    for rel in ("core/email_puller.py", "api/v1/jobs.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert not re.search(r"if i > \d+:\s*(#[^\n]*)?\n\s*break", src), rel
        assert "min(len(cdoc)" not in src, rel
