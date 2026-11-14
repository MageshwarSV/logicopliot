"""Stop a pre-alert that announces a Bill of Lading this customer already sent us.

The whole point is WHERE this runs: straight after the message is claimed, before a single
attachment is downloaded or a single page is OCR'd. The BL comes off the SUBJECT, which is
free (app/core/email_bl.py), so a repeat costs nothing at all - where letting it through
costs an OCR pass over every page, an AI classification, an extraction run, and leaves the
operator two identical jobs to tell apart.

"Already sent us" means: the same normalised BL, from the same sender, inside the window,
on a message that actually became a job. The sender matters - two different customers can
legitimately quote the same carrier reference, and the comparison is per account.

Nothing is thrown away. The raw message is kept so a person can decide this really is a
separate shipment and press "Process as new job".
"""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# How far back a BL counts as already seen. The requirement asks for 30 days and for this to
# be configurable later; it is one named constant until there is a screen that sets it.
DUPLICATE_WINDOW_DAYS = 30

DUPLICATE_DIR = "duplicate_email"


def find_original(db: Session, *, bl_number: str, sender: str,
                  window_days: int = DUPLICATE_WINDOW_DAYS, now: datetime | None = None):
    """The earlier message that already brought this BL in from this sender, or None.

    Only counts a message that actually became a JOB: one that was ignored, or that failed
    before a job existed, left nothing for a second copy to be a duplicate OF.
    """
    from app.models.email_seen import EmailSeen

    if not bl_number or not sender:
        return None
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    return (
        db.query(EmailSeen)
        .filter(
            EmailSeen.bl_number == bl_number,
            EmailSeen.sender == sender,
            EmailSeen.job_id.isnot(None),
            EmailSeen.created_at >= cutoff,
        )
        .order_by(EmailSeen.created_at.asc())
        .first()
    )


def already_queued(db: Session, message_id: str) -> bool:
    """This exact message is already sitting in the duplicate queue - a re-poll, not a new
    arrival. Without this a mailbox re-read would stack up copies of the same duplicate."""
    from app.models.duplicate_email import DuplicateEmail

    return db.query(DuplicateEmail).filter(
        DuplicateEmail.message_id == message_id).first() is not None


def record_duplicate(db: Session, *, message_id: str, sender: str, subject: str,
                     bl_number: str, original, raw: bytes | None,
                     received_at: datetime | None = None):
    """Put the message in the queue instead of making a job of it.

    `original` is the EmailSeen row find_original returned - the job this BL already belongs
    to is read off it, along with when that one arrived, which is what the screen shows so a
    person can tell a same-day re-send from a shipment genuinely repeating a month later.
    """
    from app.core.config import get_settings
    from app.models.duplicate_email import DuplicateEmail
    from app.models.job import Job

    original_job = db.get(Job, original.job_id) if original and original.job_id else None
    row = DuplicateEmail(
        message_id=message_id,
        tenant_id=original_job.tenant_id if original_job else None,
        group_id=original_job.group_id if original_job else None,
        sender=(sender or "")[:320] or None,
        subject=(subject or "")[:500] or None,
        received_at=received_at or datetime.now(timezone.utc),
        bl_number=bl_number,
        original_job_id=original_job.id if original_job else None,
        original_reference=(original_job.reference if original_job else None),
        original_received_at=getattr(original, "created_at", None),
        status="pending",
    )
    db.add(row)
    db.flush()

    # Keep the message itself. Without it "Process as new job" would have nothing to work
    # from - the attachments were deliberately never downloaded.
    if raw:
        try:
            out = Path(get_settings().uploads_dir) / DUPLICATE_DIR / row.id
            out.mkdir(parents=True, exist_ok=True)
            path = out / "original.eml"
            path.write_bytes(raw)
            row.raw_path = str(path)
        except Exception:  # noqa: BLE001 - the row is still worth having without it
            logger.exception("could not keep the raw message for duplicate %s", row.id)
    db.commit()
    logger.info(
        "duplicate BL %s from %s: %r held for review (already on %s, received %s)",
        bl_number, sender, (subject or "")[:60],
        row.original_reference or "an earlier job", row.original_received_at)
    return row


def discard_raw(row) -> None:
    """Delete the kept message. Called when the duplicate is deleted or has been processed -
    it is a full copy of the mail with every attachment, and nothing refers to it after."""
    import shutil

    try:
        if row.raw_path:
            shutil.rmtree(Path(row.raw_path).parent, ignore_errors=True)
    except Exception:  # noqa: BLE001
        logger.warning("could not remove the kept message for duplicate %s", row.id)
