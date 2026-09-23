from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CustomFilterPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Super-Admin-uploaded reference page (OCR'd once at upload), compared against every
    ingested document page by pure string similarity - never AI - so a matching page can be
    dropped before classification/extraction, the same way page_filter.py already drops
    carrier terms-and-conditions pages. Global: applies across every tenant, same as
    SystemSetting."""

    __tablename__ = "custom_filter_pages"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_text: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
