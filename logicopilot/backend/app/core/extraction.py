"""Extraction engine — pulls configured field values out of an UNSEEN document.

At configure-time (the wizard) each field got a both-mode profile: a list of caption
variations + a semantic description + an extraction prompt. At run-time (a Job) the
operator uploads a real document whose layout/captions may differ. We OCR it once, then
ask OpenAI to read the field values out of that OCR text using the per-field prompts —
so a value captioned "Waybill No" on the new doc is still found for a field the admin
marked next to "Seaway Bill of Lading No".

One OpenAI call per document (all its fields batched) keeps it cheap and coherent.
"""

import base64
import datetime as _dt
import json
import logging
import re
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_VISION_PAGES = 6


def _normalize(value: str | None) -> str:
    """Case/space/punctuation-insensitive form for comparison."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tokens(value: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _is_numeric_value(value: str | None) -> bool:
    """True when the value is dominated by digits — weights, prices, counts, IDs —
    as opposed to a name/address that merely contains a number.

    A run of three or more letters disqualifies it, however digit-heavy the rest looks.
    "1A000001550A - 38-6x24x3-7_shield-cover" is 55% digits and was being treated as a
    quantity, so it was compared number-list against number-list and reported as a mismatch
    of the very same part it describes. Codes ("1A000001550A", "CH20261122") and measurements
    ("654.320") have no such run and still take the strict numeric path.
    """
    alnum = _normalize(value)
    if not alnum:
        return False
    if re.search(r"[a-z]{3,}", alnum):
        return False
    digits = sum(c.isdigit() for c in alnum)
    return digits / len(alnum) >= 0.5


def _numbers(value: str | None) -> list[float | str]:
    raw = re.findall(r"\d+(?:\.\d+)?", (value or "").replace(",", ""))
    out: list[float | str] = []
    for x in raw:
        try:
            out.append(float(x))
        except ValueError:
            out.append(x)
    return sorted(out, key=str)


# Tried in order; the first that parses the WHOLE string wins. "%d/%m/%Y" (day first) is
# tried before "%m/%d/%Y" - this app's documents are Indian/international customs
# paperwork, where day-first is the house convention, and a day-first reading is tried
# first so an ambiguous date like 08/09/2026 resolves the same way this tenant's own
# invoices and packing lists already agree on. "%m/%d/%Y" only ever gets used for a value
# day-first can't parse at all (e.g. 04/17/2026 - no 17th month).
_DATE_FORMATS = (
    "%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y", "%d-%B-%y",
    "%Y-%m-%d",
    "%d/%m/%Y", "%d/%m/%y",
    "%d.%m.%Y", "%d.%m.%y",
    "%m/%d/%Y",
)


def _parse_date(value: str | None) -> _dt.date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            parsed = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.date()
    return None


# A field naming a COMPANY - Consignee, Exporter, Supplier Name/Address, and the like - as
# opposed to a description, dosage, or part code, where a changed word or number inside
# otherwise-similar text is exactly the disagreement compare_values must catch. Matched by
# what the label MEANS, the same way doc_sets.py's INVOICE_LABELS is, so this works for any
# template naming a party field this way - not just the ones seen so far.
_PARTY_LABEL_HINTS = (
    "consignee", "exporter", "importer", "shipper", "supplier", "buyer", "seller",
    "notify party", "party name", "company name", "manufacturer", "address",
)


def is_party_field(label: str | None) -> bool:
    key = (label or "").lower()
    return any(hint in key for hint in _PARTY_LABEL_HINTS)


def compare_values(a: str | None, b: str | None, *, party: bool = False) -> str:
    """Returns one of: 'missing' | 'match' | 'review' | 'mismatch'.
    - missing:  the field is absent on one/both documents (nothing to compare);
    - match:    identical (ignoring case/format) or numeric-equal or clearly the same text;
    - review:   text is *almost* the same — operator decides whether to accept;
    - mismatch: numeric values differ, or the text is clearly different.

    `party=True` (see is_party_field) is for a company-name/address field specifically: two
    documents rarely transcribe a company's full name-and-address block identically - one
    adds a plot number, a state, a suffix the other doesn't print - and scoring that the same
    way as a description would count the extra, legitimate detail AGAINST the match. The
    overlap is measured against the SHORTER side's own word count instead of the combined
    union, so a value that is genuinely CONTAINED within the other (plus extra detail) still
    counts as the same party. Never applied outside a party field: a changed dosage number
    inside otherwise-similar text ('35mg' vs '70mg') scores just as high by this same
    arithmetic, which is exactly the disagreement the ordinary Jaccard score exists to catch.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return "missing"
    if na == nb:
        return "match"

    # A date written two different ways is not a disagreement about WHAT it is, only about
    # how it is typeset - '12-Aug-2026' and '2026-08-12' are the same date. Tried before the
    # numeric/text paths below: a normalised date is mostly digits and would otherwise be
    # compared digit-list against digit-list (or word against word) and reported as a
    # mismatch for no real reason. Only fires when BOTH sides parse as a real date - a value
    # that merely contains slashes or dashes without being a valid calendar date falls
    # through unaffected.
    da, db = _parse_date(a), _parse_date(b)
    if da is not None and db is not None:
        return "match" if da == db else "mismatch"

    # Numeric fields → strict: any differing digit is a mismatch.
    if _is_numeric_value(a) and _is_numeric_value(b):
        return "match" if _numbers(a) == _numbers(b) else "mismatch"

    # Text fields → graded by word overlap.
    if na in nb or nb in na:
        return "match"
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return "mismatch"
    shared = len(ta & tb)
    overlap = shared / min(len(ta), len(tb)) if party else shared / len(ta | tb)
    if overlap >= 0.8:
        return "match"
    if overlap >= 0.5:
        return "review"  # almost similar — let the operator accept or not
    return "mismatch"


def values_match(a: str | None, b: str | None) -> bool:
    return compare_values(a, b) == "match"


def numeric_total(values: list) -> float | None:
    """The sum of a column of plain numbers - a weight, a piece count, a quantity - or
    None if ANY value in it isn't one (a description, a part code - text has no sensible
    total).

    Two documents can legitimately itemize the same shipment at a different granularity:
    a bill of lading states one aggregate gross weight for the whole shipment; a weight
    list breaks the same shipment down per carton. The row COUNT is expected to differ
    there - it is not a disagreement - but the total each side implies should still
    agree, and that is what this is for.
    """
    total = 0.0
    found_any = False
    for v in values:
        if not _is_numeric_value(v):
            return None
        nums = _numbers(v)
        if not nums or not isinstance(nums[0], float):
            return None
        total += nums[0]
        found_any = True
    return total if found_any else None


# A packing list has no prices on it. Asked for a Rate and an Amount anyway - because the same
# field list was configured for both documents - the reader answered "$5.63" and "USD 63.00",
# neither of which appears anywhere on the page. It had produced a plausible number rather than
# admit the field was absent, and a plausible number is the worst possible answer: it goes into
# a customs declaration looking exactly like a real one.
ABSENT_MEANS_NULL = (
    "RETURN null WHEN THE FIELD IS NOT ON THIS DOCUMENT. This is not a failure - documents of "
    "different kinds carry different fields, and a packing list genuinely has no prices, no "
    "totals and often no HS code. Do not fill a field from a value that merely looks like the "
    "right shape. Do not compute one, do not add up a column, do not carry a value across from "
    "a field of a different meaning, and do not invent a plausible one. If you cannot point at "
    "the value printed on THIS page, the answer is null. A wrong value is far worse than none: "
    "it is read as fact and filed. "
    # TRIED AND REVERTED: a rule saying "a printed caption with an empty box beside it is
    # also null". It was aimed at an empty 'Marks and No.of Container' box on the export
    # invoice, which was being filled with 10 - the customer's line serial from the table
    # underneath. It did not fix that, and it broke a field that worked: 'marks and no',
    # whose LABEL resembles that same empty caption, started returning null on a document
    # where its value was plainly printed.
    #
    # The lesson is about where a fix belongs. These rules are shared by every field of every
    # template, so one that leans on how a label reads will misfire somewhere it was never
    # aimed. A document that genuinely does not carry a field should not have that field
    # marked on it - that is a template question, not a prompting one.
)

# 'CHENNAI Port', '( USD )', 'USD 5.54' - the value with a neighbouring caption stuck to it.
# The OCR text runs labels and values together in a two-column layout, so what sits beside a
# value on the page is not part of it.
ONLY_THE_VALUE = (
    "RETURN ONLY THE VALUE ITSELF. This document's OCR runs captions and values together, so "
    "strip any caption that has come along with it - its own or a neighbouring field's. "
    "'CHENNAI Port of Discharge' is 'CHENNAI'. '( USD )' is 'USD'. 'Invoice No E26000505' is "
    "'E26000505'. Keep a unit only when it is genuinely part of the value, as in a weight. "
    "Never return the surrounding sentence when a single value was asked for."
)


# How much OCR text goes into ONE request. Not a cap on the document - a document longer than
# this is read in several passes and the answers merged, so nothing is dropped.
#
# It used to be a hard truncation: ocr_text[:12000] for fields, [:14000] for rows. Seven of this
# customer's invoices merged into one PDF come to 17,125 characters, so the reader saw about
# five of them and never knew the last two existed. No error, no warning - the line items simply
# were not there.
CHUNK_CHARS = 11000
# Enough that a field's caption and its value, or a whole table row, cannot be split by a
# boundary and lost between two passes.
CHUNK_OVERLAP = 900


def _chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split long OCR text into overlapping pieces, always on a line boundary.

    Never mid-line: a value cut in half is a value read wrong, which is worse than one read
    twice. The overlap means anything sitting on a boundary appears whole in one of the two
    pieces; duplicates are dealt with by the callers.
    """
    text = text or ""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut > start:
                end = cut
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return out


def extract_document_fields(ocr_text: str, fields: list[dict]) -> dict[str, str | None]:
    """fields: [{"label": str, "prompt": str, "variations": [str], "description": str}]
    Returns {label: value_or_None}. Falls back to all-None if OpenAI is unavailable.

    A document longer than one request's worth is read in SEVERAL passes and the answers
    merged, rather than cut off at 12,000 characters and silently half-read.
    """
    settings = get_settings()
    labels = [f["label"] for f in fields]
    if not settings.openai_api_key or not fields:
        return {label: None for label in labels}

    pieces = _chunks(ocr_text)
    if len(pieces) > 1:
        # First answer wins per field. These documents print their header fields once, near the
        # top, so the earliest pass that finds one has read it where it belongs; a later pass
        # over a second invoice in the same PDF must not overwrite it.
        logger.info("Extraction: reading %d chars in %d passes", len(ocr_text or ""), len(pieces))
        merged: dict[str, str | None] = {label: None for label in labels}
        for piece in pieces:
            part = extract_document_fields(piece, fields)
            for label in labels:
                if merged[label] in (None, "") and part.get(label) not in (None, ""):
                    merged[label] = part[label]
            if all(merged[label] not in (None, "") for label in labels):
                break            # everything found - the remaining passes cost money for nothing
        return merged

    # The SAME description of a field that every other reader gets. This one built its own,
    # shorter version - label, prompt, caption variations - and silently dropped the anchor
    # text, the example value and the format hint. Those three are the evidence captured when
    # the Super Admin drew the box, and this is the reader that runs whenever OCR succeeds,
    # which is nearly always. So the crop only ever informed the VISION fallback: drawing a box
    # carefully changed nothing for a normal job, and a field that reads correctly in the
    # wizard could read something else entirely in production.
    field_lines = _field_lines(fields)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured fields from a document's raw OCR text. "
                        "Different documents caption the same field differently — match by "
                        "meaning and by the listed caption variations. Return a JSON object "
                        "mapping each requested field name to the raw value found (string).\n"
                        + ABSENT_MEANS_NULL
                        + ONLY_THE_VALUE
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Fields to extract:\n"
                        + "\n".join(field_lines)
                        + "\n\nReturn a JSON object keyed by exactly these field names: "
                        + ", ".join(labels)
                        + "\n\n--- OCR TEXT ---\n"
                        + ocr_text
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        out: dict[str, str | None] = {}
        for label in labels:
            val = data.get(label)
            out[label] = None if val in (None, "", "null") else str(val)
        return out
    except Exception as exc:  # noqa: BLE001 — degrade to all-None on any failure
        logger.warning("Extraction failed: %s", exc)
        return {label: None for label in labels}


# The one rule both row readers share. A packing list printed ONE product row -
# "H-EY330  835791A" in the item cell and "51678625" in the PO column - and came back as THREE
# line items, one per code, because nothing said that a row is a PRODUCT rather than a value.
# The invoice read the same shipment correctly as one row with nine fields, so the job showed
# three products where there was one, two of them carrying a part number and nothing else.
ROW_IS_A_PRODUCT = (
    "COUNT THE PRODUCT ROWS FIRST, then return exactly that many objects - no more. "
    "A row of the table is one PRODUCT; it is not one value. A single product row often "
    "carries several identifiers in different columns - a part number, a customer part "
    "number, a PO number, an internal code - and two of them may sit in the SAME cell "
    "separated by spaces or a line break. They all belong to that ONE row. If more than one "
    "column or value could answer a requested field, choose the single most appropriate "
    "column and use that same column for every row. NEVER return an extra object because a "
    "value appeared in another column, or elsewhere in the same cell. A table with one "
    "product row returns exactly ONE object, however many codes are printed across it. "
    # A packing list row read '40 451512000 1 INDIA/MIP/CAMR/01 LCS-702003- 15-42 ...' and the
    # quantity came back as 1 - the tail of a PO number the printer had broken across two
    # lines. The same shape produced 74 out of a material code ending '- 74', and 7 out of
    # '451510518 7'. Every one of them sits BEFORE the goods description; none of them after.
    "IDENTIFIERS ARE OFTEN SPLIT ACROSS LINES by the printer: a purchase order number can "
    "appear as '451512000 1' and a material code as 'LCS-702003- 15-42'. Every fragment "
    "belongs to that identifier - a loose digit left at the end of one is NOT a quantity, a "
    "weight or a price. So find the goods description in the row first: the numeric columns "
    "are the values printed AFTER it, in their printed order, and everything before it belongs "
    "to the identifiers. "
    # A real export invoice's item table had two DIFFERENT products sharing one PO number
    # printed together, plus its amount printed before its description on every row instead of
    # after. An anchor built on the PO number merged those two products' rows into one and
    # dropped a product entirely; the fix is to anchor on the one column that cannot repeat -
    # the plain row count (SI.No 1, 2, 3...) - never on an identifier that can legitimately
    # appear on more than one line. The illustration below is invented data, not a real
    # shipment - it exists only to show the TECHNIQUE, never copy any of its numbers or codes
    # into an answer.
    "THE BEFORE/AFTER RULE ABOVE DOES NOT ALWAYS HOLD, AND NOT EVERY IDENTIFIER IS A SAFE ROW "
    "ANCHOR. Some tables print a row's entire cluster of values - price, quantity, rate, codes "
    "- in an order that does not follow the header's own left-to-right column order at all, "
    "with some values landing before the description and some after, in no fixed pattern. When "
    "you notice this, STOP relying on position relative to the description, and anchor on the "
    "table's SERIAL/ITEM NUMBER COLUMN instead (SI.No, Sl.No, Item No - the plain count 1, 2, "
    "3... printed once per row). This is the ONLY field safe to use as a row boundary. A PO "
    "number or a part number is NOT safe for this: either can legitimately repeat across two or "
    "more separate rows (several line items sharing one purchase order, or the same part billed "
    "under two different lines) - anchoring on one of those will wrongly merge distinct rows "
    "together or make you drop one. TECHNIQUE ONLY, not real data - never reuse these numbers: "
    "text reading '...9.5 / 1 ZZ-100 / 40.00 / ... / 2 QQ-200 / WIDGET-B / 65.00 / 9.5 / 3.0 / "
    "... / 3 QQ-200 / 12.5 / 90' is THREE rows (SI.No 1, 2, 3) even though QQ-200 appears twice "
    "(rows 2 and 3 happen to share that PO) - the trailing '12.5 / 90' is the table's own TOTAL "
    "line, not a fourth row. Segment by SI.No occurrence and count that many row-objects exactly "
    "- this is the row count from COUNT THE PRODUCT ROWS FIRST above - and never let a repeated "
    "PO number or part number convince you two rows are actually one, or let one row's value "
    "drift into a neighbouring row's object just because it printed nearer to that neighbour's "
    "description. Every value you actually RETURN must come from the document in front of you, "
    "never from this illustration."
    # A real export invoice had NO serial-number column at all - only SHIP MARKS / PACKAGES /
    # DESCRIPTION / QUANTITY / UNIT PRICE / TOTAL, one part number buried inside the
    # description cell. Two of its rows happened to share the SAME description text, the SAME
    # quantity AND the SAME unit price, differing only in that buried part number - and one
    # row's quantity was dropped as an apparent duplicate. Needing one more value to still
    # return the expected row count, the table's own grand TOTAL (USD) figure at the bottom of
    # the page was used in its place - a dollar amount, printed once for the whole table, badly
    # mistaken for a missing per-row quantity.
    "WHEN THE TABLE HAS NO SERIAL/ITEM-NUMBER COLUMN AT ALL, anchor on the goods-description "
    "block instead: each new occurrence of the row's OWN identifying code (a 'PT NO.', 'Part "
    "No.', 'Item Code' or similar line printed inside or under the description) starts a new "
    "row, even when the description text above it reads identically to the row before. TWO "
    "ROWS ARE NEVER THE SAME ROW JUST BECAUSE SEVERAL COLUMNS MATCH - a description, a "
    "quantity and a unit price can all legitimately repeat across two genuinely different "
    "products (the same component supplied under two purchase orders, say). Check the row's "
    "OWN identifying code specifically before concluding two rows are duplicates; if that code "
    "differs, they are two separate rows and BOTH quantities must be returned, however similar "
    "everything else printed on them looks. TECHNIQUE ONLY, not real data: rows printed as "
    "'Widget A / PT NO.:AA-001 / 200 PCS / 4.50' then 'Widget A / PT NO.:AA-002 / 200 PCS / "
    "4.50' are TWO rows (200 PCS each, 400 PCS total) even though the description, quantity "
    "and price are all identical - AA-001 and AA-002 are different parts. "
    "A TABLE'S OWN GRAND TOTAL - the dollar amount printed once, usually in a 'Total (USD)' or "
    "'Total (INR)' style column at the very bottom of the table - IS NEVER A PER-ROW VALUE FOR "
    "ANY COLUMN. Never substitute it for a quantity, a weight, a unit price or anything else "
    "you could not otherwise find for a row - not even when you are one value short of the row "
    "count you expected. A row whose value genuinely cannot be found on the document returns "
    "null for that row instead; a plausible-looking wrong number (the table's own total, a "
    "neighbouring row's value) is worse than admitting the value is missing, because it looks "
    "exactly like a real answer and nothing downstream can tell the difference."
)


def extract_document_rows(ocr_text: str, fields: list[dict]) -> list[dict[str, str | None]]:
    """Pull a REPEATING table out of a document: one dict per row, aligned across fields.

    Used for fields the admin ticked as "multiple values in this document" — a packing list
    or invoice line-item table. Alignment matters more than any single cell: row 3's
    description, quantity and price must come from the same physical row, so every field is
    requested in ONE call and the model is told to keep the arrays the same length.

    Returns [] when OpenAI is unavailable or the document has no such table.
    """
    settings = get_settings()
    labels = [f["label"] for f in fields]
    if not settings.openai_api_key or not fields or not ocr_text.strip():
        return []

    pieces = _chunks(ocr_text)
    if len(pieces) > 1:
        # Rows ACCUMULATE - every pass contributes its own line items and they are kept in
        # document order. Seven invoices in one PDF have seven tables, and cutting the text at
        # 14,000 characters simply lost the last two along with every product on them.
        #
        # The overlap between passes can show one row twice, so a row identical to one already
        # collected is dropped. Two genuinely identical lines on the same invoice are rare, and
        # losing one of those is far better than inventing a duplicate line item on a customs
        # entry - a repeat is a quantity error, a gap is a missing product somebody notices.
        logger.info("Row extraction: reading %d chars in %d passes",
                    len(ocr_text or ""), len(pieces))
        collected: list[dict[str, str | None]] = []
        seen: set[tuple] = set()
        for piece in pieces:
            for row in extract_document_rows(piece, fields):
                key = tuple((label, (row.get(label) or "").strip()) for label in labels)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(row)
        return collected

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=90)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=3000,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You read the LINE-ITEM TABLE out of a document's OCR text. The document "
                        "lists products, one per row. Return EVERY row, in the order they "
                        "appear, as a JSON object: {\"rows\": [ {field: value}, ... ]}. "
                        "Each row object uses exactly the requested field names. "
                        "CRITICAL: one object per PHYSICAL ROW of the table — values in the same "
                        "object must come from the same row. " + ROW_IS_A_PRODUCT
                        + "Use null for a field a row does not "
                        "have. Do NOT include the TOTAL/summary row. Do NOT merge rows. Do NOT "
                        "invent rows. If the document has no repeating table, return an empty list."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Fields to read from each row:\n"
                        + "\n".join(_field_lines(fields))
                        + "\n\nReturn {\"rows\": [...]} where each object has exactly these keys: "
                        + ", ".join(labels)
                        + "\n\n--- OCR TEXT ---\n"
                        + ocr_text
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        rows = data.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        if not rows:
            # The model sometimes answers column-wise — {"item_quantity": [...], ...} — instead
            # of row-wise, which silently cost us every row. Transpose it back: element i of
            # each array belongs to row i, which is the same alignment guarantee.
            columns = {
                label: data[label] for label in labels
                if isinstance(data.get(label), list)
            }
            if columns:
                depth = max(len(v) for v in columns.values())
                rows = [
                    {label: (vals[i] if i < len(vals) else None) for label, vals in columns.items()}
                    for i in range(depth)
                ]
                logger.info("Row extraction: transposed %d column-wise result(s)", depth)
        out: list[dict[str, str | None]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            clean = {}
            for label in labels:
                v = row.get(label)
                clean[label] = None if v in (None, "", "null") else str(v)
            # Skip rows where nothing at all was found.
            if any(v for v in clean.values()):
                out.append(clean)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Row extraction failed: %s", exc)
        return []


def _field_lines(fields: list[dict]) -> list[str]:
    lines = []
    for f in fields:
        variations = ", ".join(f.get("variations") or []) or "(none)"
        line = (
            f'- "{f["label"]}": {f.get("prompt") or f.get("description") or ""} '
            f"(may be captioned as: {variations})"
        )
        # What the Super Admin's CROP actually saw on the reference document. This is the
        # whole point of drawing a box: it is evidence about which part of the page this
        # field lives in and what its value looks like. Without it the model reads the
        # document blind and, where two candidates fit the wording, picks arbitrarily —
        # which is how a Port of Loading came back as the inland Place of Receipt.
        if f.get("anchor_text"):
            line += f"\n    On the reference document the text around this box read: \"{f['anchor_text']}\""
        if f.get("example"):
            line += (
                f"\n    On the reference document this field read: \"{f['example']}\" — use this"
                " only to recognise WHICH field is meant and the SHAPE of its value."
                " NEVER copy it as the answer: this job is a different shipment and the real"
                " value will differ."
            )
        if f.get("format_hint"):
            line += f"\n    Expected shape: {f['format_hint']}"
        # A tenant's formatting note applies to THIS column's value only. Kept on its own
        # line so it cannot be mistaken for an instruction about the reply's overall shape.
        if f.get("format"):
            line += f'\n    Format this column\'s value as: {f["format"]}'
        lines.append(line)
    return lines


def extract_document_fields_from_images(image_paths: list[Path], fields: list[dict]) -> dict[str, str | None]:
    """Vision fallback — read the field values straight from the page images with OpenAI
    when OCR (Document AI) is unavailable. Same contract as extract_document_fields."""
    settings = get_settings()
    labels = [f["label"] for f in fields]
    if not settings.openai_api_key or not fields or not image_paths:
        return {label: None for label in labels}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=90)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "Read this document's page image(s) and extract these fields. Different "
                    "documents caption the same field differently — match by meaning and the "
                    "listed caption variations. The word JSON must appear here: return a JSON "
                    "object.\n"
                    # The same two rules the OCR reader gets. This is the fallback for the very
                    # documents OCR could not read, so it is the LAST place that should be
                    # guessing - and it had the weaker wording of the two.
                    + ABSENT_MEANS_NULL
                    + ONLY_THE_VALUE
                    + "\n\nFields:\n"
                    + "\n".join(_field_lines(fields))
                    + "\n\nReturn a JSON object keyed by exactly these field names: "
                    + ", ".join(labels)
                ),
            }
        ]
        for path in image_paths[:MAX_VISION_PAGES]:
            b64 = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        resp = client.chat.completions.create(
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=900,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        out: dict[str, str | None] = {}
        for label in labels:
            val = data.get(label)
            out[label] = None if val in (None, "", "null") else str(val)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision extraction failed: %s", exc)
        return {label: None for label in labels}


def extract_document_rows_from_images(image_paths: list[Path], fields: list[dict]) -> list[dict[str, str | None]]:
    """Vision fallback for a LINE-ITEM TABLE. Same contract as extract_document_rows.

    Single fields have had an image fallback since the beginning, but rows did not: when OCR
    returned nothing, every line item was dropped with no error anywhere, while the single
    fields on the same document still came back. A partly-filled job looked like a complete one.
    """
    settings = get_settings()
    labels = [f["label"] for f in fields]
    if not settings.openai_api_key or not fields or not image_paths:
        return []

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=120)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "Read the LINE-ITEM TABLE from this document's page image(s). The document "
                    "lists products, one per row. Return EVERY row, in the order they "
                    # The word "JSON" must appear here: the API rejects response_format
                    # json_object outright if no message mentions it.
                    'appear, as a JSON object: {"rows": [ {field: value}, ... ]}. CRITICAL: one object per '
                    "PHYSICAL ROW — values in the same object must come from the same row. "
                    + ROW_IS_A_PRODUCT
                    + "Use null for a field a row does not have. Do NOT include the TOTAL/summary "
                    "row. Do NOT merge rows. Do NOT invent rows.\n\nFields to read from each row:\n"
                    + "\n".join(_field_lines(fields))
                    + "\n\nEach row object uses exactly these keys: "
                    + ", ".join(labels)
                ),
            }
        ]
        for path in image_paths[:MAX_VISION_PAGES]:
            b64 = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        resp = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0,
            max_tokens=3000,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        rows = data.get("rows")
        if not isinstance(rows, list):
            return []
        out: list[dict[str, str | None]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            clean = {label: (None if row.get(label) in (None, "", "null") else str(row.get(label))) for label in labels}
            if any(v for v in clean.values()):
                out.append(clean)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision row extraction failed: %s", exc)
        return []


def field_spec(mark, inline_format: bool = True) -> dict:
    """Everything the model is told about ONE field, from its mark. ONE implementation.

    There were three: the real job run, the wizard's demo, and its test-extract. Only the job
    run passed `example` and `anchor_text` - the evidence captured when the Super Admin drew
    the box - so the wizard's own "test this against another document" measured something the
    live system does not do. A template could look right in the wizard and read the wrong field
    in production, and nothing in either screen would say why.

    `inline_format` folds a tenant's formatting note into the instruction. Row extraction wants
    it as a per-column hint instead: its reply must keep the {"rows": [...]} shape, and a note
    like "show them separately" appended to the instruction reshapes the whole answer into
    per-column arrays, losing every row.
    """
    prompt = mark.extraction_prompt or ""
    fmt = getattr(mark, "tenant_format_prompt", None)
    if fmt and inline_format:
        prompt = f"{prompt}\nThen format the value as follows: {fmt}"
    spec = {
        "label": mark.label_name,
        "prompt": prompt,
        "variations": mark.anchor_variations or [],
        "description": mark.semantic_description,
        # Evidence from the crop. Passing it is what makes drawing a box worth more than a bare
        # text instruction: the model gets a reference for where the field sits and what its
        # value looks like, and can still reason from meaning when a new layout differs.
        "example": (mark.example_value or "")[:120],
        "anchor_text": (mark.detected_anchor or "")[:160],
        "format_hint": (getattr(mark, "value_format_hint", None) or "")[:120],
    }
    if fmt and not inline_format:
        spec["format"] = fmt
    return spec
