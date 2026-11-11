from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class NotificationRead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row = one person has seen one job's ERP outcome.

    Deliberately stores only the READ side. The notifications themselves are not rows in a
    table: they are derived, live, from the jobs the user can already see - "submitted to ERP"
    and "failed to submit to ERP" are exactly the two conditions the Jobs list's own sidebar
    already counts (_category_condition), so the bell can never drift from the screens behind
    it, and no history is needed to light it up for jobs that already exist.

    The alternative - writing a notification row at the moment of submission - would have meant
    hooking all seven places erp_status/ai_submitted_at get set (persist_run_outcome, the
    manual "it actually succeeded" confirm, three Excel-build failure paths, the startup
    sweep...). Missing one is silent: the job changes state and no notification ever appears.
    """

    __tablename__ = "notification_reads"
    __table_args__ = (
        # One read mark per person per job per kind. A job that fails, is fixed, and then
        # succeeds genuinely has two different things to tell you about, so kind is part of
        # the key - marking the failure read must not also silence the success.
        UniqueConstraint("user_id", "job_id", "kind", name="uq_notification_read"),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "submitted" | "failed" - the only two the product asks for.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
