from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class JobEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One line of a job's history: what it changed to, and when.

    A job's row only ever holds where it is NOW. An operator looking at a list can see that
    something is on ERP Entry but not when it arrived, when it was extracted, or how long it
    has been sitting - and that is exactly what they need to know to work out what to chase.

    Written by a session listener rather than by each place that moves a job, because `status`
    is set in thirteen different places across the API, the email puller and the background
    runner. Anything that has to be remembered at thirteen call sites is a thing that will be
    forgotten at the fourteenth.
    """

    __tablename__ = "job_events"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The job's status after this change: draft | processing | extracted | completed |
    # failed | duplicate. Kept as free text rather than an enum so a new status added later
    # writes history correctly instead of raising.
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Where the operator would have seen it: Documents | Verification | ERP Entry | Completed.
    # Best effort - the stage is derived from the job's documents, so it is only recorded when
    # the caller already knows it, and left null rather than guessed.
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # In plain words, for the popup: "arrived from email", "documents extracted",
    # "ERP entry failed - Importer". Never a stack trace.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
