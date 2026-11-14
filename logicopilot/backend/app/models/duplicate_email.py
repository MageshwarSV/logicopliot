from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DuplicateEmail(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A pre-alert that announces a Bill of Lading this customer already sent us.

    The same BL arriving twice from the same sender inside 30 days is a re-send - someone
    forwarding the pre-alert again, or a mail client re-delivering it - not a second
    shipment. Making a job out of it costs an OCR pass and an AI run over documents the
    customer already has a job for, and leaves the operator two identical jobs to tell apart.

    So it is stopped BEFORE any of that: the BL is read off the SUBJECT, which costs nothing
    (app/core/email_bl.py), and if it matches one already seen the message lands here instead
    of becoming a job. No attachment is downloaded, no page is OCR'd, no model is called.

    Nothing is thrown away, though. The raw message is kept at `raw_path` so a person who
    decides this really is a separate shipment can press "Process as new job" and have it go
    through the ordinary pipeline from the beginning - which is why this table exists at all
    rather than the mail simply being skipped.
    """

    __tablename__ = "duplicate_emails"

    # The RFC822 Message-ID, same key EmailSeen uses. Unique: one row per message, so a
    # re-poll of the same mail can never pile up duplicates of the duplicate.
    message_id: Mapped[str] = mapped_column(String(998), unique=True, index=True,
                                            nullable=False)

    # Taken from the ORIGINAL job, not worked out from this mail - identifying the customer
    # is what classification does, and classification is exactly what has not been run here.
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True
    )

    sender: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                         nullable=True)

    # Normalised for comparison (app/core/email_bl.normalise_bl): upper case, letters and
    # digits only, so "SHAS-00427635" and "shas00427635" are the same bill.
    bl_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # The job this BL already belongs to. SET NULL rather than CASCADE: if that job is
    # deleted the duplicate is still a real thing that happened, and the operator still has
    # to decide what to do with the mail.
    original_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    original_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                  nullable=True)

    # The untouched message, under UPLOAD_DIR - what "Process as new job" replays. Deleted
    # with the row, since nothing else ever refers to it.
    raw_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pending | processed | deleted. Only "pending" ever shows on the screen; the other two
    # are kept so the same message arriving again is still recognised as already handled
    # rather than queued a second time.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    processed_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    handled_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                        nullable=True)
