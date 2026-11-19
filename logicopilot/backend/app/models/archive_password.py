"""A zip password a customer uses, saved once by the Tenant Admin and tried automatically on every
password-protected zip in that tenant's mail and uploads (app/core/zip_passwords.py).

Stored encrypted (app/core/mail_crypto - the same as mailbox passwords), never returned by any
API after it is saved, never logged."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ArchivePassword(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "archive_passwords"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional narrowing: only this template, and/or only mail from these sender domains.
    # Either way it is still tried on everything else AFTER the matching ones.
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True
    )
    sender_domains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)        # e.g. "Flex"
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
