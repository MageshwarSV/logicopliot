"""classify_custom_page / filter_pages_with_custom / get_active_custom_filter_texts: the
Super Admin content-match page filter. Pure difflib.SequenceMatcher comparison against
admin-uploaded reference texts - never an AI call - dropping a page only when it is a
near-identical match to a stored reference page.
"""
from app.core.custom_page_filter import (
    SIMILARITY_THRESHOLD,
    classify_custom_page,
    filter_pages_with_custom,
    get_active_custom_filter_texts,
)
from app.models.custom_filter_page import CustomFilterPage

REFERENCE_PAGE = """
COMPANY LETTERHEAD - STANDARD COVER SHEET
This document is transmitted subject to our standard terms of carriage, available on
request. Please direct all queries to our documentation department.
Reference: COVER-STD-001
"""

# Same content, a few characters of OCR noise (a misread character, extra whitespace).
REFERENCE_PAGE_WITH_OCR_NOISE = REFERENCE_PAGE.replace("carriage", "carr1age").replace("  ", " ")

REAL_DATA_PAGE = """
Shipper: DTDS TECHNOLOGY PTE LTD, Blk 19 Kallang Ave, #05-153, Singapore 339410
Consignee: FLEXTRONICS INTERNATIONAL MANAGEMENT SERVICES LTD
MAWB: 160-CTU-16187986   HAWB No: CTUA2605766
"""


def test_classify_custom_page_matches_identical_text():
    is_match, why = classify_custom_page(REFERENCE_PAGE, [REFERENCE_PAGE])
    assert is_match is True
    assert "content match" in why


def test_classify_custom_page_matches_through_light_ocr_noise():
    is_match, _ = classify_custom_page(REFERENCE_PAGE_WITH_OCR_NOISE, [REFERENCE_PAGE])
    assert is_match is True


def test_classify_custom_page_does_not_match_unrelated_text():
    is_match, why = classify_custom_page(REAL_DATA_PAGE, [REFERENCE_PAGE])
    assert is_match is False
    assert why == ""


def test_classify_custom_page_with_no_reference_texts_never_matches():
    is_match, _ = classify_custom_page(REFERENCE_PAGE, [])
    assert is_match is False


def test_classify_custom_page_handles_empty_text():
    assert classify_custom_page("", [REFERENCE_PAGE]) == (False, "")
    assert classify_custom_page("   ", [REFERENCE_PAGE]) == (False, "")


def test_classify_custom_page_picks_best_match_among_several_references():
    other_reference = "SOME OTHER COMPLETELY DIFFERENT BOILERPLATE PAGE ABOUT SOMETHING ELSE"
    is_match, why = classify_custom_page(REFERENCE_PAGE, [other_reference, REFERENCE_PAGE])
    assert is_match is True
    assert "#2" in why


def test_similarity_threshold_is_tight_not_loose():
    # A merely similarly-shaped page (same rough length/subject) must NOT match - this
    # guards against the threshold being loosened to something that would false-positive
    # on two different real documents.
    similar_but_different = REFERENCE_PAGE.replace("STANDARD COVER SHEET", "SPECIAL NOTICE").replace(
        "documentation department", "customer service team")
    is_match, _ = classify_custom_page(similar_but_different, [REFERENCE_PAGE])
    assert is_match is False
    assert SIMILARITY_THRESHOLD >= 0.8  # sanity check the constant itself stays tight


def test_filter_pages_with_custom_drops_the_matching_page_and_renumbers():
    pages = [REAL_DATA_PAGE, REFERENCE_PAGE, REAL_DATA_PAGE]
    kept, dropped = filter_pages_with_custom(pages, [REFERENCE_PAGE], "test doc")
    assert dropped == [2]
    assert kept == [REAL_DATA_PAGE, REAL_DATA_PAGE]


def test_filter_pages_with_custom_never_drops_every_page():
    # Same safety net as page_filter.filter_pages, reused rather than reimplemented.
    pages = [REFERENCE_PAGE, REFERENCE_PAGE]
    kept, dropped = filter_pages_with_custom(pages, [REFERENCE_PAGE], "all boilerplate")
    assert dropped == []
    assert kept == pages


def test_filter_pages_with_custom_with_no_reference_texts_is_a_noop():
    pages = [REAL_DATA_PAGE, REFERENCE_PAGE]
    kept, dropped = filter_pages_with_custom(pages, [], "test doc")
    assert dropped == []
    assert kept == pages


def test_get_active_custom_filter_texts_returns_only_active_rows(db_session):
    db_session.add(CustomFilterPage(name="active one", reference_text="alpha text", is_active=True))
    db_session.add(CustomFilterPage(name="inactive one", reference_text="beta text", is_active=False))
    db_session.commit()

    texts = get_active_custom_filter_texts(db_session)
    assert texts == ["alpha text"]


def test_get_active_custom_filter_texts_empty_table_returns_empty_list(db_session):
    assert get_active_custom_filter_texts(db_session) == []


def test_get_active_custom_filter_texts_normalises_curly_quotes(db_session):
    db_session.add(CustomFilterPage(name="curly", reference_text="Carrier’s Tariff", is_active=True))
    db_session.commit()

    texts = get_active_custom_filter_texts(db_session)
    assert texts == ["Carrier's Tariff"]


def test_end_to_end_wiring_a_stored_reference_page_drops_a_matching_document_page(db_session):
    """Exercises the exact pattern every call site in jobs.py/email_puller.py uses: a
    Super-Admin-uploaded CustomFilterPage row, fetched once via get_active_custom_filter_texts,
    then passed into filter_pages_with_custom for a real document's pages."""
    db_session.add(CustomFilterPage(name="letterhead", reference_text=REFERENCE_PAGE, is_active=True))
    db_session.commit()

    reference_texts = get_active_custom_filter_texts(db_session)
    pages = [REAL_DATA_PAGE, REFERENCE_PAGE, REAL_DATA_PAGE]
    kept, dropped = filter_pages_with_custom(pages, reference_texts, "incoming email attachment")

    assert dropped == [2]
    assert kept == [REAL_DATA_PAGE, REAL_DATA_PAGE]
