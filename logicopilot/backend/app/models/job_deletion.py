from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class JobDeletion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Who deleted a job, written at the moment it is deleted. `created_at` is when.

    A deleted job leaves nothing behind but its billing rows - which is exactly where the
    question gets asked: the Billing page shows "Deleted Jobs: 30" and until now there was no
    way to say who removed any of them, or whether a person did at all. The job row is gone,
    so this cannot hang off it; it stands on its own, keyed by the same job_id the billing
    ledger (BillablePageEvent) keeps.

    The deleter's name, email and role are COPIED here rather than only linked: an account can
    be renamed or removed later, and "who deleted it" must still read as it was on the day.
    `deleted_by_id` is null for a job the system removed itself (`how` then says which part).

    Shown to a Super Admin only (see tenants.list_billing_jobs) - it names a member of the
    customer's own staff, and the customer's Tenant Admin sees the Billing page too.
    """

    __tablename__ = "job_deletions"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # No foreign key on purpose: the job is deleted in the same transaction.
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    deleted_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deleted_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    deleted_by_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # In plain words: "Deleted from the Jobs list", "Removed automatically by a page-filter sweep".
    how: Mapped[str | None] = mapped_column(String(255), nullable=True)
