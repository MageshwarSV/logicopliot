"""Fill a blank per-product value from another document's own table, matched on a key.

WHY THIS IS NOT THE ORDINARY FALLBACK. A line-item field marked on two documents already
collapses onto one line, first non-empty wins (see jobs.entry_values_and_rows / _by_line), and
an Excel column may chain `fallback_fields` (see excel_entry). BOTH match on POSITION: line 3
of one document is line 3 of the other.

That is wrong here, and quietly so. On JOB-7C8DA9 (ROHM air, production, 2026-11-25) the
invoice has no per-product origin at all - only one header line reading "ORIGIN: Made in
Malaysia,Philippine,Thailand,China" - while the packing list prints it under every product:

    LTR10EZPF51R0  (9401008778M01)   15,000   0.47   0.81
    (Made In Philippine-QTY:15000)

But the two documents are ordered differently. The invoice groups by P/O number and HS code;
the packing list groups by carton:

    invoice      line 1  KDZVTFTR43A    (9404000733M02)   -> packing list says MALAYSIA
    packing list line 1  LTR10EZPF51R0  (9401008778M01)   -> packing list says PHILIPPINE

Matching on position would have written PH onto a product that is MY, on a customs
declaration. A wrong country is worse than a blank one: a blank is caught by the operator and
by the ERP, a plausible wrong value is filed.

So the match is on the PART NUMBER, which both documents print and which the invoice already
stores per product as `item_material_code`.

Rules, deliberately conservative:
  * only ever fills a BLANK target - a value the invoice did carry is never touched
  * only fills when exactly ONE origin is known for that part number; a part listed twice with
    two different countries is left blank rather than guessed
  * a length guard, because a value of the wrong width is not a cosmetic problem: the ERP
    refuses the whole import over it ("ITEMS Country_of_Origin ... Entry should be of 2
    characters" rejected four jobs on 2026-11-26)
  * inert unless all four labels are present on the job, so every template without the packing
    list marks behaves exactly as it does today
"""
from __future__ import annotations

import re


def _norm_key(value: str) -> str:
    """A part number as a comparison key.

    Punctuation and spacing differ between the two documents and between OCR readings of them
    - "(9401008778M01)", "9401008778M01", "9401008778 M01" are one part. Letters and digits
    only, upper-cased. Part numbers here carry no meaningful punctuation, so nothing distinct
    is merged by dropping it.
    """
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def fill_blanks_by_key(
    rows: dict[str, list[str]],
    *,
    target: str,
    key: str,
    source_value: str,
    source_key: str,
    expect_len: int | None = None,
) -> int:
    """Fill blanks in `rows[target]` from `rows[source_value]`, matched on the part number.

    `target`/`key` are the product lines (the invoice's own table); `source_value`/`source_key`
    are the other document's own table, which has its own length and its own order.

    Returns how many blanks were filled. Changes nothing and returns 0 when any of the four
    labels is absent from the job.
    """
    if any(lbl not in rows for lbl in (target, key, source_value, source_key)):
        return 0
    targets = rows[target]
    keys = rows[key]
    src_vals = rows[source_value]
    src_keys = rows[source_key]
    if not isinstance(targets, list) or not isinstance(keys, list):
        return 0

    # part number -> the set of origins the other document gives for it. A set, not the first
    # reading: a part listed on two carton rows with two different countries is a genuine
    # ambiguity on the document itself, and guessing between them is exactly the failure this
    # module exists to avoid. ROHM lists MCR10EZPF10R0 twice, both Philippine - one entry.
    known: dict[str, set[str]] = {}
    for k, v in zip(src_keys, src_vals):
        nk = _norm_key(k)
        val = str(v or "").strip()
        if not nk or not val:
            continue
        known.setdefault(nk, set()).add(val)

    filled = 0
    for i, current in enumerate(targets):
        if str(current or "").strip():
            continue                      # the invoice carried it - never overwrite
        if i >= len(keys):
            break
        candidates = known.get(_norm_key(keys[i]))
        if not candidates or len(candidates) != 1:
            continue                      # unknown part, or the document disagrees with itself
        value = next(iter(candidates))
        if expect_len is not None and len(value) != expect_len:
            # Writing it would trade a blank the operator can fix for an import the ERP
            # refuses outright. Leave it blank and let them type it.
            continue
        targets[i] = value
        filled += 1
    return filled


# The fallbacks the product lines take, in order. Each is inert until BOTH of its source
# labels are marked on the other document, so adding one here changes nothing on a template
# that does not carry them.
#
# CountryOfOriginCode: the invoice is the source of truth and usually carries it (1,025 of
# 1,071 product lines on production). The packing list fills only what the invoice left blank.
LINE_FALLBACKS: tuple[dict, ...] = (
    {
        "target": "CountryOfOriginCode",
        "key": "item_material_code",
        "source_value": "pl_origin",
        "source_key": "pl_part_code",
        "expect_len": 2,
    },
)


def apply_line_fallbacks(rows: dict[str, list[str]]) -> dict[str, int]:
    """Run every configured line fallback over a job's rows, in place.

    Returns {target label: how many blanks were filled}, for the log - empty when nothing was
    filled, which is the normal case for a job whose invoice carried everything.
    """
    out: dict[str, int] = {}
    for rule in LINE_FALLBACKS:
        try:
            n = fill_blanks_by_key(rows, **rule)
        except Exception:  # noqa: BLE001 - a fallback must never break reading a job
            continue
        if n:
            out[rule["target"]] = out.get(rule["target"], 0) + n
    return out
