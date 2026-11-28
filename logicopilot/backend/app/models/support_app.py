from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SupportKnownIssue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Support mobile app (2026-11-28): the fix the desk wrote ONCE for a kind of failure.

    `key` is app.core.support_app.issue_key() - e.g. "erp_field:country of origin",
    "erp_login", "missing_documents". Every open problem with the same key shows this fix."""

    __tablename__ = "support_known_issues"

    key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    fix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SupportDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A support person's phone that gets push alerts (Expo push token)."""

    __tablename__ = "support_devices"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    push_token: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
