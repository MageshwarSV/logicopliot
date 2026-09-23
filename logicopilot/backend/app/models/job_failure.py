from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class JobFailureReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A failed web-entry (ERP) run — raised to the Super Admin inbox with the company,
    ERP login, operator, and job so it can be investigated."""

    __tablename__ = "job_failure_reports"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    job_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    erp_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # failed steps + log tail
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open | resolved
