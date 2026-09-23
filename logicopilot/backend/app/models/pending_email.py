from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PendingEmail(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A pulled email whose documents did not identify a customer on their own (or fit more
    than one), held here with its attachments so a person can pick the template by hand
    instead of the mail being read once and then thrown away.

    EmailSeen already records that a message was looked at, so the poller never re-reads it;
    this table is the separate, actionable trace of THAT specific kind of miss — the
    attachments live on disk under UPLOAD_DIR/pending_email/{id}/ until resolved or
    dismissed, at which point they are deleted.
    """

    __tablename__ = "pending_emails"

    message_id: Mapped[str] = mapped_column(String(998), unique=True, index=True, nullable=False)
    sender: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # no_customer | ambiguous — why identify_customer could not place this on its own.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # [{"name", "ext", "path", "text"}] — path is relative to UPLOAD_DIR; text is the OCR
    # already paid for, kept so resolving does not re-read the attachment from scratch.
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # pending | resolved | dismissed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    resolved_group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True
    )
    resolved_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
