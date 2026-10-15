from sqlalchemy import ForeignKey, JSON, String, Text
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
    # Set instead of `files` for a row created via the "upload this OR say why not" flow (see
    # upload_supporting_document_or_reason) - a typed reason there is the operator's genuine
    # answer, not a missing upload, and needs to read as one on IRN Processing's own document
    # list exactly the way a real attached file would.
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
