from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class BillablePageEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One durable, append-only record of pages actually sent through OCR/rendering - written
    once, at the exact moment a document's page_count is computed (every _render_pages() call
    site: email-pulled job creation, a manual upload or re-upload on the Document Capture page,
    Smart Upload, and assigning a staged Unclassified page to a slot).

    This exists because JobDocument.page_count is NOT safe to bill from directly: deleting a
    page zeroes or removes its row (see _remove_document_file in app/api/v1/jobs.py), and old
    jobs are hard-deleted entirely (an operator's own DELETE /jobs/{id}, and the automatic
    old-job sweep in custom_filter_pages.py) - in both cases the AI-processing cost was already
    incurred the moment the page was read, so it must still bill even after the page or the
    whole job is later deleted.

    Deliberately has NO foreign key to Job (job_id is just an indexed string) - a job this
    references can be deleted later, and this ledger must survive that untouched. job_reference
    and shipment_type are snapshotted at write time for the same reason: looking them up from
    Job/TemplateGroup later would fail once the job is gone."""

    __tablename__ = "billable_page_events"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    shipment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # True only for an explicit admin re-run of an already-processed job's classification
    # (reclassify_documents) - a genuinely fresh re-read of pages already billed once before,
    # not just a job's own normal multi-pass OCR (a large file split across an initial pass and
    # a later extension past the per-file cap is still ONE original pass, not a rerun). False
    # for every other call site: Smart Upload, email-pulled jobs, and a manual upload/re-upload
    # on Document Capture are all still "first time this content was billed", by request.
    is_rerun: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
