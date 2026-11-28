from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SupportActivity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Production Support's own log (S4, 2026-11-27): a note on a job, a customer call, an
    issue marked handled, a Retry pressed, an alert raised. Shown as Recent Activity and on
    Issues & Resolution. Cross-tenant: tenant/job are optional (a call may be about a
    customer in general; an alert about a server)."""

    __tablename__ = "support_activity"

    # note | customer_call | handled | retry_erp | retry_extraction | alert
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # A note's status (support app, 2026-11-28): investigating | fixed | customer_informed |
    # erp_side | not_our_issue - or None for a plain note / any other kind.
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # alert de-duplication key ("server-down:erp"), or None
    alert_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
