"""Content-match page filter: a Super Admin uploads a reference page (OCR'd once at
upload), and any ingested document page whose text is near-identical to it is dropped
before classification/extraction - the same mechanism as page_filter.py's hardcoded
carrier-terms rules, but admin-managed and program comparison only, never AI.
"""
import difflib

from sqlalchemy.orm import Session

from app.core.page_filter import _normalise, filter_pages
from app.models.custom_filter_page import CustomFilterPage

# Near-identical/identical boilerplate, not loose semantic similarity - SequenceMatcher's
# ratio() on two literally-equal strings is 1.0; real OCR noise (whitespace, a misread
# character here and there) knocks a couple of points off even the SAME page reprinted.
# 0.85 is deliberately tight: this must never catch two merely-similar invoices, only a
# genuinely repeated boilerplate page.
SIMILARITY_THRESHOLD = 0.85


def get_active_custom_filter_texts(db: Session) -> list[str]:
    """Every active reference page's OCR text, normalised once. Call ONCE per extraction
    pass (per job / per email / per upload batch) - never inside a per-page or per-document
    loop - and pass the result into filter_pages_with_custom for every document in that
    pass."""
    rows = (
        db.query(CustomFilterPage.reference_text)
        .filter(CustomFilterPage.is_active.is_(True))
        .all()
    )
    return [_normalise(text) for (text,) in rows if text and text.strip()]


def classify_custom_page(text: str, reference_texts: list[str]) -> tuple[bool, str]:
    """(is_match, why) - True when `text` is a near-identical content match to ANY active
    reference page. Pure difflib.SequenceMatcher comparison on normalised text; no AI call."""
    if not text or not text.strip() or not reference_texts:
        return False, ""
    body = _normalise(text)
    best_ratio, best_idx = 0.0, -1
    for i, ref in enumerate(reference_texts):
        ratio = difflib.SequenceMatcher(None, body, ref).ratio()
        if ratio > best_ratio:
            best_ratio, best_idx = ratio, i
    if best_ratio >= SIMILARITY_THRESHOLD:
        return True, f"custom filter page #{best_idx + 1}: {best_ratio:.2f} content match"
    return False, ""


def filter_pages_with_custom(
    pages: list[str], reference_texts: list[str], document_name: str = "document",
) -> tuple[list[str], list[int]]:
    """Drop a page when EITHER page_filter's hardcoded carrier-terms rule OR a Super-Admin
    custom filter page matches it. Same return contract, same never-drop-everything safety
    net as page_filter.filter_pages (reused, not reimplemented)."""
    classifier = (lambda t: classify_custom_page(t, reference_texts)) if reference_texts else None
    return filter_pages(pages, document_name, extra_classifier=classifier)
