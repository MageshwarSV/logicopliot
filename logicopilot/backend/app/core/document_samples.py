"""Learning what a customer's documents look like, from where they put them.

The classifier's hardest problem is that nothing in the WORDING of shipping
paperwork reliably separates one document from another. Every document in a
consignment quotes every other - the bill of lading says "packing list and
commercial invoice attached", the invoice states the ocean freight it prepaid,
and all four carry the same B/L number, vessel, package count and weights.
Measured on 693 real documents, phrase matching answered 4 of them.

What does separate them is how one particular customer lays them out, and the
customer tells us that every time they put a file into a slot by hand. That is a
correct, free, customer-specific label, and it was being discarded.

So: record the top of each manually-placed document against the slot it was
placed in, and show those back to the model next time it has to decide. The
prompt already has a place for exactly this - `reference`, described to the model
as "the customer's own sample of this document" - which nothing had ever filled.

Correcting a misfile is the most valuable case of all. Deleting a file from the
wrong slot and uploading it to the right one says precisely "not that, this", and
the upload is what gets recorded.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.models.document_sample import DocumentSample

logger = logging.getLogger(__name__)

# How much of a document to keep. It has to be small enough to sit in a prompt
# alongside several others, and the identifying part of a document is its top -
# letterhead, title, first field labels. Further down is shipment data that
# changes with every consignment and would only teach the model noise.
DOCUMENT_SAMPLE_CHARS = 700

# How many to keep per slot, and how many to show the model.
#
# Several rather than one because a customer may file the same slot from more
# than one supplier, each with its own layout; a single sample would describe one
# of them and quietly contradict the rest. Not many more because each one costs
# prompt space on every classification, and the newest are the ones that reflect
# how this customer works now.
MAX_SAMPLES_PER_SLOT = 5
SAMPLES_SHOWN = 3

# Too short to have learned anything from - a near-empty OCR, or a scan that
# failed. Storing one would push a real sample out of the window.
MIN_USEFUL_CHARS = 60


# Lines that describe the SENDER rather than the document. A supplier's invoice and
# their packing list carry the identical letterhead, so these teach the model who sent
# the paperwork - which it cannot use to tell one document from the other.
_SENDER_LINE = re.compile(
    r"\b(co\.?,?\s*ltd|pvt|private limited|limited|inc\.?|gmbh|corp|llc|"
    r"road|street|avenue|floor|district|province|tel|fax|e-?mail|@|"
    r"http|www\.|zip|postal|p\.?o\.? box)\b", re.I)

# A value, not a label: mostly digits, a date, a reference number.
_MOSTLY_DIGITS = re.compile(r"^[\W\d]*$")


def _label_lines(text: str) -> list[str]:
    """The lines that say what KIND of document this is.

    Field labels, taken from the whole page rather than the top of it, with their
    values stripped. "Net Weight 820.50 KGS" becomes "net weight kgs" - the same on
    every packing list this supplier ever sends, and absent from every invoice.

    Values are removed on purpose: 820.50 changes with each shipment and would make
    two samples of the same document look different.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = " ".join(raw_line.split())
        if not line or _SENDER_LINE.search(line):
            continue
        # Strip the data, keep the wording.
        stripped = re.sub(r"[\d.,:/\\-]+", " ", line).lower()
        stripped = re.sub(r"[^a-z&%() ]+", " ", stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if len(stripped) < 3 or len(stripped) > 60 or _MOSTLY_DIGITS.match(stripped):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        out.append(stripped)
    return out


def _trim(text: str) -> str:
    """What this document type looks like, small enough to put in a prompt.

    The title area is kept first - a document that does announce itself should say so
    up front - followed by the label lines that distinguish it from its siblings.
    """
    labels = _label_lines(text)
    if not labels:
        return ""

    out, used = [], 0
    for line in labels:
        out.append(line)
        used += len(line) + 1
        if used >= DOCUMENT_SAMPLE_CHARS:
            break

    joined = "\n".join(out)[:DOCUMENT_SAMPLE_CHARS]
    return joined if len(joined) >= MIN_USEFUL_CHARS else ""


def remember(db: Session, *, tenant_id: str, template_document_id: str,
             text: str, job_document_id: str | None = None) -> None:
    """Record that a document looking like this belongs in this slot.

    Does not commit - the caller owns the transaction, like every other
    write-then-let-the-caller-commit helper here.

    Never raises into the caller. This runs on the back of a successful upload,
    and failing to learn from one is not a reason to fail the upload itself - the
    operator's file is already saved and they would have no idea what went wrong.
    """
    try:
        excerpt = _trim(text)
        if not excerpt:
            return

        existing = (
            db.query(DocumentSample)
            .filter(DocumentSample.template_document_id == template_document_id)
            .order_by(DocumentSample.created_at.desc())
            .all()
        )

        # The same document filed twice teaches nothing the first one did not, and
        # would crowd out a genuinely different supplier's layout.
        #
        # Rows added earlier in this same transaction are checked too. A query only
        # sees what has been flushed, so two uploads sharing a transaction would each
        # look like the first and both be stored.
        pending = [o for o in db.new if isinstance(o, DocumentSample)
                   and o.template_document_id == template_document_id]
        for old in [*existing, *pending]:
            if old.excerpt == excerpt:
                return

        db.add(DocumentSample(
            tenant_id=tenant_id,
            template_document_id=template_document_id,
            excerpt=excerpt,
            job_document_id=job_document_id,
        ))

        # Keep the window to the newest. A customer who changes forwarder should
        # stop being described by the old one's paperwork within a few jobs.
        for stale in existing[MAX_SAMPLES_PER_SLOT - 1:]:
            db.delete(stale)

        logger.info("document samples: learned a %s-char sample for slot %s",
                    len(excerpt), template_document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not record a document sample for slot %s: %s",
                       template_document_id, exc)


def for_slots(db: Session, template_document_ids: list[str]) -> dict[str, str]:
    """The samples to show the model, as {template_document_id: text}.

    Newest first, and separated so the model sees them as several distinct
    examples rather than one run-on document.
    """
    if not template_document_ids:
        return {}
    try:
        rows = (
            db.query(DocumentSample)
            .filter(DocumentSample.template_document_id.in_(template_document_ids))
            .order_by(DocumentSample.created_at.desc())
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        # A missing table on a server that has not migrated yet must not take
        # classification down with it; it just means nothing has been learned.
        logger.warning("could not read document samples: %s", exc)
        return {}

    grouped: dict[str, list[str]] = {}
    for row in rows:
        seen = grouped.setdefault(row.template_document_id, [])
        if len(seen) < SAMPLES_SHOWN:
            seen.append(row.excerpt)

    return {
        key: "\n--- another example ---\n".join(texts)
        for key, texts in grouped.items() if texts
    }
