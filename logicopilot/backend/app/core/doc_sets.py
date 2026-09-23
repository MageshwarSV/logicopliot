"""Pair each invoice with its own packing list, by what is printed on them.

A job can carry three invoices and three packing lists. They are uploaded in whatever order
the operator has them, so position tells you nothing: the first invoice's packing list may
well be the third file in its slot.

Pairing by position would still produce a green tick — it would just be checking invoice 1
against packing list 3. A verification that confirms the wrong two documents is worse than no
verification at all, because it is believed. So the pairing is made on the invoice number the
documents themselves carry, and a document whose number matches nothing is left unpaired and
said to be unpaired, rather than being quietly attached to whatever was next in the list.

Nothing here names a customer or a template. The labels are matched by meaning, the same way
_CUSTOMER_LABELS does it, so this works for any template that captures an invoice number.
"""
from __future__ import annotations

import re

# The invoice number, in the words customs paperwork uses. Ordered best-evidence-first.
INVOICE_LABELS = (
    "commercial invoice no", "commercial invoice number",
    "invoice no", "invoice number", "invoice num", "invoice ref",
    "inv no", "inv number",
    "invoice",
)

# A number is only a usable key if there is enough of it to be distinctive. "1" appears on
# every document ever printed; "505" is worth matching on.
MIN_KEY_LEN = 3


def _norm_label(label: str | None) -> str:
    key = re.sub(r"[^a-z ]+", " ", (label or "").lower()).strip()
    return re.sub(r"\s+", " ", key)


def normalise_key(value: str | None) -> str:
    """'E26/000505 ' and 'e26-000505' are the same invoice. Punctuation and case are how a
    document was typeset, not part of the number."""
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def pairing_key(values: dict[str, str | None]) -> str | None:
    """The invoice number this document carries, or None if it does not carry one.

    `values` is label -> extracted value for ONE uploaded file.
    """
    by_label: dict[str, str] = {}
    for label, val in values.items():
        if val and str(val).strip():
            by_label.setdefault(_norm_label(label), str(val).strip())

    for want in INVOICE_LABELS:
        if want in by_label:
            key = normalise_key(by_label[want])
            if len(key) >= MIN_KEY_LEN:
                return key
    # Looser pass: "supplier invoice no", "invoice no / date".
    for want in INVOICE_LABELS:
        for label, val in by_label.items():
            if want in label:
                key = normalise_key(val)
                if len(key) >= MIN_KEY_LEN:
                    return key
    return None


def _same_number(a: str, b: str) -> bool:
    """Exact match, or one number printed in full and the other abbreviated.

    A packing list often prints 'INV NO: 505' where the invoice itself says 'E26000505'.
    Only a suffix counts, and only when the short form is long enough to be distinctive —
    a prefix match would make E26000505 and E26000507 the same document.
    """
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= MIN_KEY_LEN and len(short) < len(long) and long.endswith(short)


def assign_sets(files: list[dict]) -> dict[str, int | None]:
    """Work out which set each uploaded file belongs to.

    `files` is one dict per uploaded file, in upload order:
        {"id": ..., "template_document_id": ..., "file_index": int, "key": str | None}

    Returns file id -> set number (1-based), or None for a file that belongs to the job as a
    whole rather than to one invoice — a single bill of lading covering all three invoices is
    the ordinary case, and it must NOT be forced into set 1.

    Sets are numbered by the order the keyed files were uploaded, so set 1 is the first
    invoice the operator attached, and that ordering carries through to the workbook.
    """
    # One file per slot is one set, full stop - there is nothing here TO pair. This has to be
    # checked before anything about the invoice-number keys, not only when none of them exist:
    # a packing list's own "Invoice No"-captioned field sometimes reads a different reference
    # than the invoice's own (a packing-list number under a similar caption, an OCR slip, a
    # genuinely different internal reference), without the two documents being unrelated. Found
    # live: exactly that mismatch split a single-invoice job's own invoice and packing list into
    # two fake sets, so every "Invoice vs Packing List" cross-check compared a real value against
    # nothing on the other side and came back a wall of false "Missing" results.
    if all(_count(files, f) == 1 for f in files):
        return {f["id"]: 1 for f in files}

    keyed = [f for f in files if f.get("key")]
    if not keyed:
        return {f["id"]: None for f in files}

    # Sets are numbered in the order the INVOICES were attached — not the order files
    # happened to arrive across all slots, which would let a packing list uploaded first
    # decide that invoice 507 is set 2. The invoice slot is the one carrying the most
    # numbers; ties go to whichever slot the template lists first, which is the order
    # `files` arrives in.
    order: list[str] = []
    counts: dict[str, int] = {}
    for f in keyed:
        tid = f.get("template_document_id")
        if tid not in counts:
            counts[tid] = 0
            order.append(tid)
        counts[tid] += 1
    primary = max(order, key=lambda t: (counts[t], -order.index(t)))

    numbers: list[str] = []

    def _add(f):
        if not any(_same_number(f["key"], n) for n in numbers):
            numbers.append(f["key"])

    for f in sorted((x for x in keyed if x.get("template_document_id") == primary),
                    key=lambda f: (f.get("file_index") or 0)):
        _add(f)
    # A number that appears only outside the primary slot still deserves a set of its own,
    # rather than being dropped — an extra packing list with no invoice is a real situation
    # and the operator needs to see it, not lose it.
    for f in sorted((x for x in keyed if x.get("template_document_id") != primary),
                    key=lambda f: (f.get("file_index") or 0)):
        _add(f)

    out: dict[str, int | None] = {}
    for f in files:
        key = f.get("key")
        if not key:
            out[f["id"]] = None
            continue
        hits = [i for i, n in enumerate(numbers, start=1) if _same_number(key, n)]
        # Ambiguous is unpaired. Better to say "this one needs a look" than to guess.
        out[f["id"]] = hits[0] if len(hits) == 1 else None
    return out


def _count(files: list[dict], f: dict) -> int:
    return sum(1 for x in files if x.get("template_document_id") == f.get("template_document_id"))


def describe_sets(files: list[dict], sets: dict[str, int | None]) -> list[str]:
    """Plain-English lines for the run log, so an operator can see what was paired with what
    without opening the database."""
    lines: list[str] = []
    numbered = sorted({v for v in sets.values() if v})
    for n in numbered:
        members = [f for f in files if sets.get(f["id"]) == n]
        names = ", ".join(str(f.get("name") or f["id"][:8]) for f in members)
        lines.append(f"set {n}: {names}")
    loose = [f for f in files if sets.get(f["id"]) is None]
    if loose:
        names = ", ".join(str(f.get("name") or f["id"][:8]) for f in loose)
        lines.append(f"whole job (not tied to one invoice): {names}")
    return lines
