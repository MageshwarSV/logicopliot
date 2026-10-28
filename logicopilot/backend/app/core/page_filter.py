"""Drop carrier terms-and-conditions pages from a document's OCR before extraction.

A Blue Anchor sea waybill is 2 pages: page 1 carries every value we need (4,384 characters)
and page 2 is 39,572 characters of carrier terms — 90% of the document, none of it data. That
noise is not harmless: it crowded the Invoice out of the ruling's budget entirely, and its
letterhead and prose are where `vessel_name` picked up "Blue Anchor" (the carrier brand) and
the ruling latched onto "FREIGHT COLLECT".

The page position is NOT fixed — a BL may arrive with 4 pages and the terms sitting anywhere —
so every page is tested on its own content. Pure pattern matching, no AI: the decision must be
identical on every run and explainable from the text.

Two independent rules, either of which drops a page:

1. SIGNATURE — distinctive clause headings and legal phrases from a carrier's terms. Measured
   across 17 real pages (3 bills of lading, 3 freight certificates, invoices, packing lists):
   a terms page hits 25 markers, every data page hits 0 or 1. The threshold of 6 sits in that
   gap with room to spare — a freight certificate legitimately mentioning "DANGEROUS GOODS",
   or a BL form printing "Port to Port Transport", scores 1 and is never touched.

2. SHAPE — for a carrier whose wording the signature does not know. Terms pages are long,
   almost digit-free, dense with legal phrasing and full of numbered clause headings, whereas
   data pages carry weights, dates, codes and container numbers. All four conditions must hold.

Safety net: never drop every page. If a document looks entirely like boilerplate the filter
stands down and passes it through, because a wrongly dropped page silently loses real data.
"""

import hashlib
import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Distinctive of a carrier's terms and conditions. Deliberately NOT included: "Non-Negotiable"
# (printed on the face of the waybill) and "Clause" alone (page 1 says "See Clause 7.3").
_SIGNATURE_PATTERNS = [
    r"trading as Blue Anchor Line",
    r"Hague-?Visby Rules",
    r"York-?Antwerp Rules",
    r"BOTH-TO-BLAME COLLISION",
    r"PARTIAL INVALIDITY",
    r"JURISDICTION AND LAW",
    r"SUB-CONTRACTING AND INDEMNITIES",
    r"TEMPERATURE CONTROLLED CARGO",
    r"DANGEROUS GOODS",
    r"NON-NEGOTIABILITY",
    r"DECK CARGO",
    r"METHODS AND ROUTE OF TRANSPORTATION",
    r"CONTRACTING PARTIES",
    r"CARRIER'S TARIFF",
    r"INSPECTION OF GOODS",
    r"VARIATION OF THE CONTRACT",
    r"COLLECTION AND DELIVERY OF THE GOODS",
    r"MERCHANT'S WARRANTIES",
    r"Indemnify the Carrier",
    r"Sub-?Contractor",
    r"howsoever caused",
    r"whatsoever nature",
    r"demurrage or detention",
    r"General Average",
    r"Port to Port Transport",
    r"burden of proof",
    r"limitation of liability",
    r"servant or agent of the Carrier",
]
_SIGNATURE = [re.compile(p, re.IGNORECASE) for p in _SIGNATURE_PATTERNS]
_SIGNATURE_MIN = 6

# Shape rule: "12. DANGEROUS GOODS" style headings, and legal vocabulary.
_CLAUSE_HEADING = re.compile(r"(?:^|\n)\s{0,8}\d{1,2}[.)]\s+[A-Z][A-Z'’&/,()\- ]{3,45}\s*(?:\n|$)")
_LEGAL_PHRASE = re.compile(
    r"\b(the Merchant|the Carrier|Clause \d|liabilit\w*|howsoever|whatsoever|hereunder|herein"
    r"|thereof|indemnif\w*|jurisdiction|arbitrat\w*|notwithstanding|shall be deemed)\b",
    re.IGNORECASE,
)
_SHAPE_MIN_CHARS = 1500
_SHAPE_MAX_DIGIT_PCT = 2.0
_SHAPE_MIN_LEGAL_PER_1K = 4.0
_SHAPE_MIN_HEADINGS = 4


def _normalise(text: str) -> str:
    """Curly quotes to straight, so "Carrier’s Tariff" matches "Carrier's Tariff"."""
    return text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def classify_page(text: str) -> tuple[bool, str]:
    """(is_boilerplate, why). `why` names the rule and its evidence, for the audit trail."""
    if not text or not text.strip():
        return False, ""
    body = _normalise(text)
    length = len(body)

    hits = [pattern for pattern, rx in zip(_SIGNATURE_PATTERNS, _SIGNATURE) if rx.search(body)]
    if len(hits) >= _SIGNATURE_MIN:
        return True, f"signature: {len(hits)} carrier-terms markers ({', '.join(hits[:4])}…)"

    if length >= _SHAPE_MIN_CHARS:
        digit_pct = sum(c.isdigit() for c in body) * 100 / length
        legal_per_1k = len(_LEGAL_PHRASE.findall(body)) * 1000 / length
        headings = len(_CLAUSE_HEADING.findall(body))
        if (
            digit_pct <= _SHAPE_MAX_DIGIT_PCT
            and legal_per_1k >= _SHAPE_MIN_LEGAL_PER_1K
            and headings >= _SHAPE_MIN_HEADINGS
        ):
            return True, (
                f"shape: {length} chars, {digit_pct:.1f}% digits, "
                f"{legal_per_1k:.1f} legal phrases/1k, {headings} clause headings"
            )
    return False, ""


def filter_pages(
    pages: list[str],
    document_name: str = "document",
    extra_classifier: Callable[[str], tuple[bool, str]] | None = None,
) -> tuple[list[str], list[int]]:
    """Split a document's per-page OCR into (pages to use, 1-based page numbers dropped).

    `extra_classifier` is tried on any page the built-in carrier-terms rule doesn't already
    flag - e.g. the Super Admin's content-match custom filter pages (app/core/
    custom_page_filter.py). It shares this function's own safety net (never drop every
    page) instead of each caller reimplementing one.
    """
    if not pages:
        return pages, []

    verdicts = []
    for p in pages:
        v = classify_page(p)
        if not v[0] and extra_classifier is not None:
            v = extra_classifier(p)
        verdicts.append(v)
    drop = {i for i, (is_boiler, _) in enumerate(verdicts) if is_boiler}

    # Never strip a document down to nothing — better a noisy document than a missing one.
    if drop and len(drop) == len([p for p in pages if p and p.strip()]):
        logger.warning(
            "%s: every page looked like carrier terms — keeping all %d, filter stood down",
            document_name, len(pages),
        )
        return pages, []

    for i in sorted(drop):
        logger.warning(
            "%s page %d: dropped %d chars of carrier terms — %s",
            document_name, i + 1, len(pages[i]), verdicts[i][1],
        )
    return [p for i, p in enumerate(pages) if i not in drop], sorted(i + 1 for i in drop)


def kept_page_to_original(page_count: int, dropped_pages: list[int]) -> list[int]:
    """The original 1-based page number for each entry of filter_pages()'s kept list, in
    order. classify_document() reports pages by their position in that kept, renumbered
    text ("=== PAGE n ===") - this is what translates one of those numbers back to a real
    page of the source PDF, for actually splitting it.
    """
    drop = set(dropped_pages)
    return [p for p in range(1, page_count + 1) if p not in drop]


# The header filter_pages()' output is joined with, and the only thing separating one page's
# text from the next once a file has been flattened into a single string.
_PAGE_MARKER_RE = re.compile(r"^===\s*PAGE\s+(\d+)\s*===$")

# Below this many letters-and-digits a page is not distinctive enough to match on: "page 1 of
# 2" and a date are shared by half the paperwork in a shipment.
_MIN_FINGERPRINT_CHARS = 40


def claim_fingerprint(file_text: str, pages: list[int]) -> str:
    """What the pages of ONE claim actually say, reduced to a comparable fingerprint.

    Two different attachments can carry the same document. A supplier sends "Invoice.pdf"
    holding the invoice and the packing list, then sends "Packing List.pdf" holding the same
    two - and the classifier, correctly, says both files contain both types. The result was
    the Invoice slot holding two invoices and the Packing List slot holding two packing
    lists, all four being the same two pieces of paper (JOB-3DB0E9).

    Comparing the files on disk does not catch it: each slot's pages are split out of their
    own source, so the two Invoice PDFs came to 26,816 bytes each with DIFFERENT sha256s -
    identical content, different PDF timestamps. The text is what is actually the same.

    Normalised to survive the parts that differ without meaning anything: case, and the
    whitespace and line breaks that shift when the same page is OCR'd out of two files.
    Letters and digits are kept exactly, so two genuinely different invoices - which differ
    in their numbers, dates and amounts - never collide. Three real invoices for one shipment
    still produce three fingerprints and three rows, which is the behaviour this must not
    break.

    Returns "" when those pages carry no readable text at all, and a caller must treat that
    as "cannot tell" rather than as a match - otherwise every unreadable scan would dedupe
    itself against every other one.
    """
    if not file_text or not pages:
        return ""
    wanted = set(pages)
    collected: list[str] = []
    current: int | None = None
    for line in file_text.splitlines():
        marker = _PAGE_MARKER_RE.match(line.strip())
        if marker:
            current = int(marker.group(1))
            continue
        if current in wanted:
            collected.append(line)
    body = re.sub(r"[^a-z0-9]+", " ", "\n".join(collected).lower()).strip()
    if len(body) < _MIN_FINGERPRINT_CHARS:
        # Too little to be sure of. A handful of characters is shared by half the paperwork
        # in a shipment ("page 1 of 2", a date), so matching on it would merge real
        # documents.
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def extract_pdf_pages(blob: bytes, original_pages: list[int]) -> bytes:
    """A new, standalone PDF holding only the given 1-based ORIGINAL page numbers, in order.

    For a combined file - one PDF carrying both the Invoice and the Packing List - each slot
    must get only ITS pages, not the whole thing: writing the same unsplit file into both
    slots left every field-extraction reading from pages that were never meant for it.
    """
    import fitz  # PyMuPDF

    with fitz.open(stream=blob, filetype="pdf") as src, fitz.open() as out:
        for p in original_pages:
            if 1 <= p <= src.page_count:
                out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        return out.tobytes()


def merge_pdf_pages(sources: list[tuple[bytes, list[int]]]) -> bytes:
    """A new, standalone PDF combining pages pulled from SEVERAL different original PDFs, in
    the order given - each source's own 1-based ORIGINAL page numbers, same convention as
    extract_pdf_pages above. Built for Smart Upload: several separately-uploaded files that
    turn out to carry the same invoice number land as one multi-page document instead of one
    document per file (see app.core.doc_sets)."""
    import fitz  # PyMuPDF

    with fitz.open() as out:
        for blob, original_pages in sources:
            with fitz.open(stream=blob, filetype="pdf") as src:
                for p in original_pages:
                    if 1 <= p <= src.page_count:
                        out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        return out.tobytes()
