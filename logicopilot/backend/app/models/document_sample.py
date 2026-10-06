from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class DocumentSample(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What one of this customer's documents of this type actually looks like.

    Every time an operator puts a file into a named slot by hand, they have stated
    what that document is - more reliably than any classifier can infer it. That is
    a free, correct, customer-specific label, and until now it was thrown away.

    These are kept so the classifier can be shown them: "the last three things this
    customer filed as their Packing List began like this". A supplier's invoice and
    their packing list carry the same letterhead and quote the same B/L number, so
    nothing in a document's wording distinguishes them reliably in general - but
    within one customer's paperwork the two are laid out consistently, and that is
    what these capture.

    Why an excerpt and not the whole document: it goes into a prompt, so it has to
    be small, and the identifying part of a document is its top - the letterhead,
    the title, the first field labels. The rest is shipment data that changes every
    time and would only mislead.

    Correcting a misfiled document is the most valuable case. An operator deleting
    a file from the wrong slot and uploading it to the right one is saying exactly
    "not that, this" - and the right one is the upload, which is what is recorded.
    """

    __tablename__ = "document_samples"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The slot the operator chose. The whole point of the record.
    template_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The top of the document, OCR'd. Trimmed before storing - see DOCUMENT_SAMPLE_CHARS.
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    # Kept for support: which job taught us this, so a bad sample can be traced back
    # to the upload that created it.
    job_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
