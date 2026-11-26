"""Indian Customs Tariff (ICEGATE) - the global CTH master (2026-11-26).

One list for the whole platform: every tenant, every stream. No tenant_id on purpose. It is the
set of 8-digit CTH codes that are allowed at all - an AI suggestion, an operator's pick and
anything sent to an ERP must be one of these (see app/core/customs_tariff.py).

It sits BESIDE the customer's own Reference CTH Dump (material_master.py), which keeps doing
exactly what it did: part code / description -> CTH.

A replacement upload never deletes a code: a code missing from the new file is marked inactive,
so a job that already carries it still shows what it is.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CustomsTariffUpload(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One upload of the master file - the version history on the SaaS Admin page."""

    __tablename__ = "customs_tariff_uploads"

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_codes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CustomsTariff(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One 8-digit CTH code of the Indian Customs Tariff."""

    __tablename__ = "customs_tariff"

    cth: Mapped[str] = mapped_column(String(8), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preferential_rate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # The upload that last added or changed this row.
    upload_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("customs_tariff_uploads.id", ondelete="SET NULL"), nullable=True,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
