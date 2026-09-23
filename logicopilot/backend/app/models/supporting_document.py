from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SupportingDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An arbitrary file (or set of files) an operator attaches to a job under a name they
    choose - GK1's IRN Documents Upload stage, and GK2's IRN Processing. Purely storage: never
    OCR'd, never classified, never fed into extraction. A job can have any number of these,
    each with its own label and one or more files."""

    __tablename__ = "supporting_documents"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # [{"stored_as": "0_coo.pdf", "original_name": "COO.pdf", "size": 12345}, ...] - however
    # many files were uploaded under this one label.
    files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
