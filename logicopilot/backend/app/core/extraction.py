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
from app.core.llm import create_chat_completion_with_retry

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
    # A leading "-" counts as a sign only when it is NOT itself preceded by a letter/digit/
    # underscore - the negative lookbehind is what keeps "INV-2026-001" reading as [2026,
    # 1], the same two positive numbers it always has, rather than [-2026, -1] the moment
    # minus-sign support was added: those hyphens are separators inside an identifier, not
    # a sign, and the one thing telling the two apart is what sits immediately before the
    # "-". A genuine negative ("-50.00", or one appearing after a space/colon/anything
    # else that isn't a word character, as in "Adjustment: -50.00") is unaffected. Found
    # live: compare_values("-50", "50") read as a match, and numeric_total(["-50", "50"])
    # came out 100.0 instead of 0.0 - any credit-note/adjustment figure with a minus sign
    # was silently losing it.
    raw = re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", (value or "").replace(",", ""))
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
    # _normalize strips ALL punctuation, including a leading minus sign - "-50" and "50"
    # would otherwise reach the equality check right below as the identical "50" and
    # incorrectly report "match", losing a credit-note/adjustment figure's sign entirely.
    # Checked first, and narrowly: only when exactly one side actually starts with a minus
    # (so "-50" vs "-60", where the shortcut below was never going to fire wrongly in the
    # first place, is untouched, and an ordinary hyphenated identifier like "INV-2026-001"
    # or a date is unaffected either way, since this only ever returns early when both
    # sides are ALSO classified as numeric).
    if ((a or "").strip().startswith("-") != (b or "").strip().startswith("-")
            and _is_numeric_value(a) and _is_numeric_value(b)):
        return "match" if _numbers(a) == _numbers(b) else "mismatch"
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
    "Never return the surrounding sentence when a single value was asked for. "
    # A container line printed '1x 20GP CONTAINER   1020 KG   SNBU2369717' came back as
    # '2SNBU2369717' - the leading digit of the NEARBY count/size phrase ('1x20GP') got glued
    # onto the front of the real container number, which is always exactly 4 letters then 7
    # digits. Generalized: whenever a field's format is stated (a fixed length, a fixed
    # pattern), a candidate that runs longer or shorter than that shape almost always means a
    # neighbouring word or number was captured along with it, not that this value is an
    # exception to its own format.
    "WHEN A FORMAT IS GIVEN, MATCH IT EXACTLY. A container number is always 4 letters then 7 "
    "digits - never more, never fewer. Any other field with a stated length or pattern works "
    "the same way: an extra character stuck to either end is evidence it was fused with "
    "unrelated adjacent text (a count, a size, a neighbouring code), not a value that merely "
    "runs a little long. Trim a candidate back to its own stated shape rather than return it "
    "exactly as it happened to print. "
    # Asked for a Port of Loading's 5-character UN/LOCODE - the prompt's own worked examples
    # list included 'Hong Kong HKHKG' - the reader still answered 'HK', the port's 2-letter
    # COUNTRY code, one that happens to be the first two letters of its own longer UN/LOCODE.
    # This is the SAME shape of error as the container number above (an answer shorter than
    # its stated length), just in the other direction: not an extra character glued on, but a
    # more specific code truncated down to a shorter, more general one that merely starts the
    # same way.
    "THIS CUTS BOTH WAYS: an answer SHORTER than its stated length is just as wrong as one "
    "too long, and often means a more general code (a country code, a prefix) was returned in "
    "place of the specific one asked for. A field asking for a 5-character UN/LOCODE is never "
    "satisfied by a 2-character country code, even when the two happen to share their first "
    "letters - Hong Kong's UN/LOCODE is HKHKG, not HK; HK is only its country code. Always "
    "count the characters in what you are about to return against the stated length before "
    "answering."
)

# A voyage number printed 'O45E' (the LETTER O) came back as '045E' (the DIGIT 0) - one mark's
# own prompt already warned about exactly this ('keep it a letter, never rewrite it as the
# digit 0'), and it still happened. The same confusion recurs on any alphanumeric code, not
# only a voyage number, and repeating the warning inside one field's own prompt text was not
# enough to make it reliable - it needed to be said once, with authority, for every field.
LETTERS_ARE_NOT_DIGITS = (
    "IN ANY CODE OR REFERENCE NUMBER, NEVER SWAP A LETTER FOR A DIGIT THAT LOOKS SIMILAR, OR "
    "BACK. The letter O is not the digit 0, the letter I is not the digit 1, the letter S is "
    "not the digit 5, the letter B is not the digit 8, the letter Z is not the digit 2, and the "
    "letter G is not the digit 6 - in either direction. A voyage number, a reference code, a "
    "waybill number or any other identifier keeps exactly the letters and digits it was printed "
    "with. TECHNIQUE ONLY, not real data: a voyage number printed 'O45E' stays O45E, never "
    "045E - the first character is the LETTER O because it sits among other letters in a code, "
    "not the number zero, however similar the two look printed."
)

# The exact worked example inside one field's own prompt ('a date written 6/12/2026 means 6
# December 2026, NOT 12 June') was still answered backwards, AND in the wrong output format
# entirely - a per-field instruction repeated on several different date marks word-for-word
# was not enough on its own. Promoted to a shared, authoritative rule every date field gets,
# rather than depending on each mark's own prompt text carrying the same warning reliably.
DATE_DAY_FIRST_ISO = (
    "ANY DATE FIELD IS READ DAY FIRST, THEN MONTH, THEN YEAR, AND RETURNED AS YYYY-MM-DD. This "
    "applies whether the printed date uses a slash, a dash or a dot as its separator. TECHNIQUE "
    "ONLY, not real data: a date printed 6/12/2026 is the 6th of December 2026 - day 6, month "
    "12 - and is returned as 2026-12-06, never as 2026-06-06 and never in any other format such "
    "as '12-Jun-2026' or '06/12/2026'. When the first number is greater than 12 the order is "
    "unambiguous either way - use it as printed. Always return the ISO form YYYY-MM-DD, never "
    "the month name, never the original punctuation, whatever format the question's own wording "
    "uses to describe the field."
)

# An invoice printed 'SELLER  Cisco Systems, Inc. / 170 W Tasman Dr / San Jose CA 95134 /
# United States' beside 'SHIP FROM  Schenker Singapore Pte Ltd / 20 Alps Avenue, Level 4 /
# Singapore 498747' - two side-by-side address blocks. The FIRST fix for this (treat the two
# blocks as sealed, non-overlapping units) still failed on the REAL document, because OCR did
# not print one block then the other - it printed the page row-band by row-band ACROSS both
# columns, and a THIRD, unrelated caption pair ('PAYMENT TERMS' / 'ORDER TYPE') shared a
# row-band with these two addresses, landing its own values physically BETWEEN the street
# line and the city line of both addresses, before the two addresses' own country line ('UNITED
# STATES' / 'SINGAPORE') finally appeared at the very end of the whole cluster. Confirmed
# against this exact document's own real OCR text before writing this version.
TWIN_BLOCKS_STAY_WHOLE = (
    "A CLUSTER OF SEVERAL CAPTION/VALUE PAIRS CAN BE STACKED TOGETHER AND READ ROW-BAND BY "
    "ROW-BAND ACROSS TWO COLUMNS AT ONCE - not one caption's whole block, then the next "
    "caption's whole block, but a LEFT value, a RIGHT value, a LEFT value, a RIGHT value, and "
    "so on, row by row down the page. When this happens, a caption's own later lines (a city, "
    "a postal code, a country) can be separated from its earlier lines (its name, its street) "
    "by one or more ENTIRELY UNRELATED caption/value pairs that merely happen to share a "
    "row-band with it - those unrelated pairs' values must be skipped, never stitched into the "
    "address you are assembling.\n"
    "THE FIX: identify which column (left or right) belongs to the caption you were asked "
    "about, then follow that SAME column down through the WHOLE cluster, collecting only ITS "
    "OWN lines in the order they appear and skipping every line that belongs to the other "
    "column or to a different, unrelated caption pair in between - even when that means "
    "reaching several lines further down the page than where the caption itself was printed, "
    "and even past an unrelated pair's own values sitting in the middle. A cluster like this "
    "only ends where a genuinely NEW section heading begins (e.g. 'BILL TO' / 'SHIP TO' "
    "starting the NEXT cluster) - not at the first unrelated pair encountered inside it.\n"
    "TECHNIQUE ONLY, not real data: a cluster printed as 'SELLER   SHIP FROM / Acme Corp   "
    "Global Freight Ltd / 100 Main St   5 Dock Rd / PAYMENT TERMS   ORDER TYPE / Boston MA   "
    "Rotterdam / NET 30   DOMESTIC / UNITED STATES   NETHERLANDS' has the Seller's COMPLETE "
    "address as '100 Main St, Boston MA, United States' - built from the LEFT column's three "
    "address lines only, skipping straight over the 'PAYMENT TERMS' / 'NET 30' pair in the "
    "middle, which belongs to a completely different field. Never '100 Main St, 5 Dock Rd, "
    "Boston MA, United States' or any other blend that borrows so much as one line from the "
    "right column or from the unrelated pair between them."
)

# A run of Air Import jobs kept returning null for Importer/Supplier/MAWB/HAWB/weight/HS-code
# fields even though the job plainly had a document attached - because that document was not
# the carrier's air waybill or the supplier's own invoice, but the clearing agent's OWN
# internally-generated customs-filing summary ('CheckList - BILL OF ENTRY'), restating the same
# facts as plain Label / Value lines in a completely different layout. Every field's prompt
# above is written for the ORIGINAL document (a real air waybill's Shipper/Consignee boxes, a
# real invoice's header and item table) and found nothing on this substitute, even though the
# same information was sitting right there under a different heading. This document type
# recurs constantly - any clearing agent's own back-office system can generate one - so it
# needs the same standing recognition as a real waybill or invoice, not a fix on one job.
CHECKLIST_SUBSTITUTE_DOCUMENT = (
    "SOME DOCUMENTS ARE A CLEARING/CUSTOMS AGENT'S OWN SUMMARY, NOT THE ORIGINAL PAPERWORK. A "
    "page headed 'CheckList - BILL OF ENTRY' (or similarly titled) is the clearing agent's own "
    "internally-generated customs-filing summary standing in for the carrier's waybill and the "
    "supplier's invoice - it is not itself a waybill or an invoice, but it restates the SAME "
    "facts as plain 'Label   Value' lines, and is just as valid a source for every field below "
    "as the original document it stands in for. On a page shaped like this, match these labels "
    "to their equivalent field, and do not apply a rule written for a REAL waybill's own boxes "
    "or a REAL invoice's own header - this page already states everything plainly under its own "
    "labels instead: 'Importer Detail' (the company named under it) is the importer/consignee; "
    "'Supplier Name' and 'Supplier Addr' are the supplier's name and address exactly as "
    "labelled, with no shipper/consignee box to tell apart; 'Supplier Country' is the "
    "supplier's country; 'MAWB No.' and 'HAWB No.' are the master and house air waybill "
    "numbers, each followed by its own 'dt. <date>' issue date; 'Port Of Loading' is the port/"
    "airport of loading; 'Gross Weight' is the shipment's gross weight; 'No Of Pkgs' is the "
    "package count; 'Cntry Of Origin' is the country of origin of the goods; 'Invoice Detail' / "
    "'Inv No & Date' is the invoice number and date; 'Invoice Value' is the invoice/total "
    "amount; 'TOI' is the incoterm. Its own 'ITEM DETAILS' table lists one product per row "
    "under columns headed 'SI No', 'RITC' (the HS/tariff code), 'Description', 'Qty', 'Unit', "
    "'Product Value', 'Unit Price' and 'Assessable Value' - read a line-item field from there "
    "exactly as you would from a commercial invoice's own item table."
)

# A Bill of Lading printed 'BOOKING NUMBER' then 'SEA WAYBILL NUMBER' as two captions in a row
# with no value between them, followed later by their two values in a row - the OCR text had
# lost the table's column lines, so neither value sits directly beside its own caption. Asked
# for the Sea Waybill Number, the reader returned the FIRST of the two values every time: the
# Booking Number, a wrong customs reference filed as the right one. This can strand ANY
# field's label away from its value on a document like this, not only this one pair.
SEPARATED_LABELS_AND_VALUES = (
    "WHEN LABELS AND VALUES HAVE BEEN SEPARATED BY OCR, PAIR THEM BY POSITION. A table whose "
    "column lines were lost often prints as several CAPTION lines in a row, with nothing "
    "between them, followed later by the SAME NUMBER of VALUE lines in a row - a value is "
    "never directly beside its own caption in this shape. Count the captions, count the "
    "values, and pair them by position: the 1st caption's value is the 1st of the values that "
    "follow, the 2nd caption's value is the 2nd, and so on - never just the first or the most "
    "plausible-looking value in the group, and never one borrowed from a DIFFERENT caption "
    "group elsewhere on the page. TECHNIQUE ONLY, not real data: text reading 'BOOKING NUMBER "
    "  SEA WAYBILL NUMBER   ABCD1234567   WXYZ7654321' has two captions then two values in the "
    "same order - Booking Number pairs with ABCD1234567 (the 1st value), Sea Waybill Number "
    "pairs with WXYZ7654321 (the 2nd value). Asked for the Sea Waybill Number here, the answer "
    "is WXYZ7654321, never ABCD1234567, however similar the two codes look."
)

# A Bill of Lading's table printed 'GROSS WEIGHT (KGS)' then 'MEASUREMENT (CBM)' as two column
# captions, then '3244   39.6' as their two values, then 'KGS   CBM' as the units - OCR had
# split each number from its own unit and printed the units together, afterward, in the same
# order. Asked for the Gross Weight, the reader returned 39.6 - the Measurement/CBM figure -
# because nothing told it a number's OWN unit is the one printed at its shared position in that
# separated unit list, not the field whose caption merely sounds similar or sits nearby.
UNIT_LABELS_PAIR_BY_POSITION = (
    "WHEN A FIELD'S LABEL NAMES A UNIT (Gross Weight in KGS, Measurement/Volume in CBM, a "
    "quantity in PCS, and so on), CONFIRM THE NUMBER YOU RETURN CARRIES THAT SAME UNIT. A table "
    "can print several numbers together and their units together, separately, in the same "
    "order - 'KGS' is always a weight, never a volume; 'CBM'/'M3' is always a volume, never a "
    "weight - so pair the Nth number with the Nth unit abbreviation and use THAT to decide "
    "which field it answers, not proximity on the page or which caption it happens to sit "
    "nearest. TECHNIQUE ONLY, not real data: a table printing 'GROSS WEIGHT (KGS)  "
    "MEASUREMENT (CBM)' as captions, then '3244   39.6' as values, then 'KGS   CBM' as units, "
    "has the 1st number (3244) paired with the 1st unit (KGS) and the 2nd number (39.6) paired "
    "with the 2nd unit (CBM) - Gross Weight is 3244, never 39.6, because 39.6 carries CBM, a "
    "volume unit, and a weight field can never be answered with a volume figure."
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
        resp = create_chat_completion_with_retry(
            client,
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
                        + LETTERS_ARE_NOT_DIGITS
                        + DATE_DAY_FIRST_ISO
                        + TWIN_BLOCKS_STAY_WHOLE
                        + SEPARATED_LABELS_AND_VALUES
                        + UNIT_LABELS_PAIR_BY_POSITION
                        + CHECKLIST_SUBSTITUTE_DOCUMENT
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
    "exactly like a real answer and nothing downstream can tell the difference. "
    # A Vietnamese supplier's invoice printed Descriptions | PO No | Quantity | Unit Price |
    # Amount - one product shipped under three different PO numbers, one row per PO, all
    # three sharing the SAME description and material code. The PO No column sits AFTER the
    # description, so it read like a chain of per-row identifiers worth counting one row per
    # distinct value the same way a serial number would be - a fourth, phantom row got
    # invented from what was really that column's own entry, and quantities and PO numbers
    # were shuffled between the genuine rows in the process. The table had a plain "No."
    # column the whole time (1, 2, 3) that named the true row count exactly.
    "A COLUMN OF DISTINCT-LOOKING ALPHANUMERIC CODES AFTER THE DESCRIPTION - a PO Number, an "
    "order reference, a batch number - IS NOT A SAFE ROW ANCHOR EITHER, for the same reason a "
    "part number is not (see above): the SAME product can legitimately ship under several "
    "different PO numbers, one row per PO, all sharing one description and one material code. "
    "Never count one row per distinct value in a column like this. When the table ALSO prints "
    "a genuine SERIAL/ITEM NUMBER column (SI.No, Sl.No, No., Item No), that count is "
    "authoritative - use it, however many different PO/order values appear alongside it, and "
    "never invent an extra row just because one more distinct code showed up than rows you had "
    "already counted. TECHNIQUE ONLY, not real data: rows printed as '1 / Widget A / PO-100 / "
    "300 PCS / 4.50' then '2 / Widget A / PO-200 / 500 PCS / 4.50' are TWO rows (No. 1 and No. "
    "2) - never a third, and never let the PO column's own value bleed into the quantity or "
    "material-code field of a neighbouring row just because both are alphanumeric codes. "
    # The SAME Vietnamese supplier's invoice, confirmed against the actual page image: its
    # Net Weight, Gross Weight, Carton count, a packing remark ("6pcs/sheet") and a Layer
    # count were each printed ONCE for the three PO rows of that one product, visually
    # centred next to the MIDDLE row rather than repeated on every row - real merged cells,
    # because those packing details are per-PRODUCT, not per-PO/line. Rows 1 and 3 print
    # nothing at all in those columns of their own. That lone "4" and that "6" each turned
    # up as a DIFFERENT row's quantity - the model, finding rows 1 and 3 "missing" a value
    # in what it expected to be a filled-in column, borrowed the nearest number on the page
    # instead of returning null.
    "A COLUMN CAN BE MERGED ACROSS SEVERAL ROWS AND PRINTED ONLY ONCE - typically a packing "
    "detail (net/gross weight, carton count, a packing remark, a layer count) that applies to "
    "the whole PRODUCT rather than to one specific PO/line, shown visually centred next to the "
    "middle of the rows it covers rather than repeated on each one. A row with no printed "
    "value of its own for a column like this returns null for that column - it does NOT borrow "
    "the merged value that was printed next to a DIFFERENT row, and that merged value must "
    "NEVER be used to fill a gap in a totally different field (a missing quantity, a missing PO "
    "number) on ANY row just because it is the nearest number on the page. Each row's own "
    "quantity, unit price, PO number and amount are printed once PER ROW, right there on that "
    "row - a merged cell that really belongs to a neighbouring row, or to the product as a "
    "whole, is never a substitute for one of those. "
    # A packing list with 20 real line items came back with 22: the last two "rows" were its
    # own SUBTOTAL and GRAND TOTAL lines, printed as one more ruled line each at the bottom of
    # the exact same table, with numbers sitting in the same columns quantity/amount do on a
    # real row - nothing marked them as different at a glance, only their own wording did.
    "A ROW THAT SUMS THE ROWS ABOVE IT IS NEVER A PRODUCT ROW, however table-shaped it looks. "
    "Recognise one by EITHER of two signs: (1) its first non-empty cell reads TOTAL, SUBTOTAL, "
    "GRAND TOTAL, NET AMOUNT, or AMOUNT IN WORDS (in any case, with or without a colon) - this "
    "is true even when every other cell on that line is filled with numbers that look exactly "
    "like a row's own quantity/price/amount; or (2) every cell on the line is empty except one, "
    "which holds a number - a total figure sitting alone in the table's rightmost/amount column "
    "with no description, no code, no quantity beside it. TECHNIQUE ONLY, not real data: a "
    "table with SI.No rows 1 through 18, the last one reading '18 / WIDGET-R / 40 PCS / 9.00 / "
    "360.00', followed by one more line reading 'GRAND TOTAL / / / / 4,820.00' with no SI.No of "
    "its own, is EIGHTEEN product rows - not nineteen. The GRAND TOTAL line is never a 19th "
    "row, however many digits it has, and COUNT THE PRODUCT ROWS FIRST above must never include "
    "it in that count."
)


def _row_count_hint(expected_row_count: int | None) -> str:
    """The one extra sentence expected_row_count adds to a row-extraction prompt - empty
    string (no change at all to the prompt) when it's None, which is every call site that
    doesn't have Document AI's own detected table geometry to offer."""
    if not expected_row_count:
        return ""
    return (
        f"\n\nThis document's table layout was independently detected to contain exactly "
        f"{expected_row_count} data row(s) (not counting any total/summary row). Return "
        f"exactly {expected_row_count} row-object(s)."
    )


def extract_document_rows(
    ocr_text: str, fields: list[dict], expected_row_count: int | None = None,
) -> list[dict[str, str | None]]:
    """Pull a REPEATING table out of a document: one dict per row, aligned across fields.

    Used for fields the admin ticked as "multiple values in this document" — a packing list
    or invoice line-item table. Alignment matters more than any single cell: row 3's
    description, quantity and price must come from the same physical row, so every field is
    requested in ONE call and the model is told to keep the arrays the same length.

    expected_row_count: the row count Document AI's own table geometry independently detected
    for this document (see app/core/docai.py's _table_row_count) — passed through by
    run_extraction when known, so the model reads with the same anchor a human proofreader
    would have (the actual printed row count), rather than guessing it purely from context.
    None (the default, and always the case for a chunked pass below — see _chunks) reproduces
    the exact prompt this function has always sent.

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
        #
        # expected_row_count is deliberately NOT passed through to these per-chunk calls: it
        # names the WHOLE document's row count, and no single chunk knows how many of those
        # rows are its own - passing it down would tell every chunk to return the full count.
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
        resp = create_chat_completion_with_retry(
            client,
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
                        + LETTERS_ARE_NOT_DIGITS
                        + DATE_DAY_FIRST_ISO
                        + TWIN_BLOCKS_STAY_WHOLE
                        + CHECKLIST_SUBSTITUTE_DOCUMENT
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Fields to read from each row:\n"
                        + "\n".join(_field_lines(fields))
                        + "\n\nReturn {\"rows\": [...]} where each object has exactly these keys: "
                        + ", ".join(labels)
                        + _row_count_hint(expected_row_count)
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
                    + LETTERS_ARE_NOT_DIGITS
                    + DATE_DAY_FIRST_ISO
                    + TWIN_BLOCKS_STAY_WHOLE
                    + CHECKLIST_SUBSTITUTE_DOCUMENT
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

        resp = create_chat_completion_with_retry(
            client,
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


def extract_document_rows_from_images(
    image_paths: list[Path], fields: list[dict], expected_row_count: int | None = None,
) -> list[dict[str, str | None]]:
    """Vision fallback for a LINE-ITEM TABLE. Same contract as extract_document_rows, including
    expected_row_count (see its docstring there) - None reproduces the exact prompt this
    function has always sent.

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
                    "row. Do NOT merge rows. Do NOT invent rows."
                    + LETTERS_ARE_NOT_DIGITS
                    + DATE_DAY_FIRST_ISO
                    + TWIN_BLOCKS_STAY_WHOLE
                    + CHECKLIST_SUBSTITUTE_DOCUMENT
                    + "\n\nFields to read from each row:\n"
                    + "\n".join(_field_lines(fields))
                    + "\n\nEach row object uses exactly these keys: "
                    + ", ".join(labels)
                    + _row_count_hint(expected_row_count)
                ),
            }
        ]
        for path in image_paths[:MAX_VISION_PAGES]:
            b64 = base64.b64encode(path.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

        resp = create_chat_completion_with_retry(
            client,
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
