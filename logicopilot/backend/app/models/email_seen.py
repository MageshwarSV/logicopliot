from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EmailSeen(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per message the puller has already examined, so it is never examined twice.

    Reading a message costs real money and time: every attachment is OCR'd page by page and the
    text is then put to a model to decide which customer it belongs to. A message that matches
    nobody would pay that on EVERY poll, for as long as it sits in the mailbox - and a mailbox
    accumulates newsletters forever.

    Marking the mail \\Seen would also stop the repeat, but it is the wrong tool: it hides the
    message from the person who owns the mailbox, and it throws away the one thing that makes a
    late fix possible - pulling again after the customer is finally configured. So the mail is
    left exactly as it is and the fact that we looked is recorded here instead.

    Keyed on the RFC822 Message-ID, which the sending server assigns and which survives the
    message being re-fetched, re-flagged or moved between folders.
    """

    __tablename__ = "email_seen"

    # Message-ID as it appears in the headers. Unique: seeing it again means "already examined".
    message_id: Mapped[str] = mapped_column(String(998), unique=True, index=True, nullable=False)

    sender: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # What we decided. One of:
    #   matched        - a job was created (job_id is set)
    #   no_customer    - the documents did not identify any customer we know
    #   ambiguous      - identified more than one and could not choose
    #   no_documents   - nothing readable was attached
    #   unreadable     - attachments could not be opened or OCR'd
    #   error          - something failed; kept so it is not retried forever
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)

    # Set only when verdict == "matched".
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    # In plain words, for whoever asks "why was my email ignored?". This is the whole reason a
    # row is kept rather than the message simply being flagged.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What the documents said about themselves, so a human can see what it was working from
    # without re-running the OCR. Short - a few names and numbers, not the page text.
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
