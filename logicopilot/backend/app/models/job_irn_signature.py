from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class JobIrnSignature(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One document header's DSC-signed file + IRN number, for a job GK1 sent down the
    "Approval for IRN" path (Job.irn_approval_requested, parked by gk2_approve in
    gk2_status == "irn_document_process"). One row per header, never per file - a header can
    hold several unsigned files but only one signed replacement.

    doc_ref says WHICH header: a job template document's own template_document_id for a
    captured document (Bill of Lading, Invoice, ...), or "supporting-<id>" for a Supporting
    Document. See app/api/v1/public_irn.py's _document_groups, the one place both kinds are
    merged into a single list."""

    __tablename__ = "job_irn_signatures"
    __table_args__ = (UniqueConstraint("job_id", "doc_ref", name="uq_job_irn_signature_doc_ref"),)

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    irn_number: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_as: Mapped[str] = mapped_column(String(255), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
