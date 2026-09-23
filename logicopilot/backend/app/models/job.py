from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
# Imported for its side effect as much as its name: the relationship below refers to
# "JobEvent" as a string, which only resolves once the class is registered. Without this
# any module that imports Job alone fails to map. job_event.py imports nothing from
# here, so there is no cycle.
from app.models.job_event import JobEvent


class Job(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One run of a configured template set against a real transaction's documents.
    Created by an Operator; visible to the Operator's tenant and to Super Admins."""

    __tablename__ = "jobs"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(255), nullable=False)  # operator's label, e.g. shipment no
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | extracting | extracted | processing | completed | failed | duplicate
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # If set (e.g. an email-pulled job), only this operator (plus admins) sees the job.
    assigned_operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Manual stage override for one job (Documents | Verification | ERP Entry | Completed).
    # When set, it wins over the computed stage.
    stage_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Cross-doc-link ids the operator explicitly accepted despite a review/mismatch flag.
    accepted_verifications: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # The operator pressed "Approved and Proceed" on Data Validation for THIS run of the
    # data. Cleared whenever extraction runs again, since a re-extraction can change the
    # very values that were approved. A job with no cross-doc checks at all would otherwise
    # sail through Data Validation with nobody ever having looked at it.
    validation_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # GK2's OWN independent "Approved and Proceed" on Data Validation, on their separate
    # re-review pass - see JobDocument.gk2_approved for why this is a second column rather
    # than reusing validation_approved.
    gk2_validation_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The GK1 (operator) -> GK2 (Gate Keeper 2) sign-off chain, layered on top of an already
    # "extracted" job rather than added as more job.status values - job.status still says
    # what actually happened to the data (extracted, or a real ERP outcome, if that pipeline
    # ever runs for this job); this says where the approval chain has gotten to.
    #   None            -> not yet submitted for GK2 approval (still with the operator)
    #   "pending"        -> Pending GK2 Approval (operator pressed Final Submit for Approval)
    #   "preparing_erp"  -> AI - Preparing for ERP (GK2 pressed Final Approve & Proceed)
    #   "submitted"      -> AI - ERP Submitted (5s after preparing_erp, on a background
    #                       thread - a placeholder for now, not a real ERP run)
    gk2_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # GK1's IRN Documents Upload stage (see app/models/supporting_document.py): set once an
    # operator has either uploaded at least one supporting document there, or explicitly
    # skipped it - the stage is optional, but one of those two actions is what ticks it off,
    # and unlike the old client-only "irnApproved" state this survives a page reload.
    irn_documents_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A GK1-set target date for this job - "YYYY-MM-DD", plain calendar date with no time
    # component (a job has no timezone of its own). Set from the Jobs list; drives the ETA
    # boxes on the operator dashboard. None = no target date set.
    eta_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # The keyed-out result for the whole job, as one document -> {label: value} object
    # (plus a "Custom" entry for the computed/hardcoded fields). job_field_values remains
    # the source of truth — this is the assembled snapshot, for export and for handing to
    # the ERP without re-joining rows.
    extracted_keyouted_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # A fingerprint of what the extracted documents actually SAY (see _compute_content_
    # checksum in api/v1/jobs.py) - set once extraction finishes, regardless of whether a
    # match was found. Lets the NEXT job compare against this one even if this one was never
    # itself flagged.
    content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Set when this job's checksum matched an already-settled job at extraction time - status
    # goes to "possible_duplicate" and everything downstream (ruling, review) waits until the
    # operator decides Approve (a genuinely separate shipment) or Delete. Kept even after
    # Approve, as the audit trail of what it was flagged against.
    duplicate_of_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    # Values read back OUT of the ERP after entry — the reference it generated (Bill of Entry
    # number, ICEGATE acknowledgement) as captured by get_text steps. Without this a completed
    # job carried no ERP-side identifier and could not be reconciled afterwards.
    erp_captured: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # The ERP run's own record. These were previously set on the response object only, so a
    # page reload erased every trace of what the engine did — and with it the failed field the
    # operator needs to correct before retrying.
    erp_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    erp_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    erp_diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    erp_final_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    erp_failed_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
    erp_failed_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deleting a job deletes its history. The column's ondelete="CASCADE" only fires where the
    # database enforces foreign keys, which SQLite does not by default - so a deleted job left
    # its history behind as orphan rows. Say it at the ORM too, and it holds everywhere.
    events: Mapped[list["JobEvent"]] = relationship(
        JobEvent, cascade="all, delete-orphan", lazy="selectin",
    )
    erp_log: Mapped[list | None] = mapped_column(JSON, nullable=True)

    documents: Mapped[list["JobDocument"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    field_values: Mapped[list["JobFieldValue"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A real uploaded file fulfilling one of the template set's declared documents."""

    __tablename__ = "job_documents"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A slot can hold several files: three invoices are three rows here, all sharing one
    # template_document_id. file_index keeps them in a stable order (0 is the slot created
    # with the job); set_index says which invoice-set this file was paired into, and is
    # filled in AFTER extraction by matching invoice numbers - never by upload order,
    # because the operator can upload invoice 1 and packing list 3 in any sequence.
    file_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    set_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Raw OCR for this uploaded document, kept in the database rather than only in the
    # on-disk page cache: {"document","doc_type","page_count","pages":[...],"text"}.
    # Survives the uploads folder being cleared and can be queried.
    extracted_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The operator pressed "Approved and Proceed" on THIS document, on Data Extraction.
    # Reset to False whenever the job is (re-)extracted — a fresh read can change what is
    # on the page, so a stale approval must not carry over.
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # GK2's OWN independent approval of this document, on their separate re-review pass.
    # Deliberately a second column rather than reusing `approved` - GK1 and GK2 are two
    # separate sign-offs, so GK2 opening a job GK1 already approved must see everything as
    # unapproved from THEIR side and press through it themselves, not inherit GK1's ticks.
    gk2_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    job: Mapped["Job"] = relationship(back_populates="documents")


class JobFieldValue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An extracted (and optionally corrected) value for one configured field on this job."""

    __tablename__ = "job_field_values"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable: a custom/computed field value has no mark or source document.
    mark_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("field_marks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    template_document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # WHICH uploaded file this value was read from. template_document_id only says which
    # SLOT (Invoice), and with three invoices in that slot their values were landing on top
    # of one another - three different invoice numbers all labelled the same thing, with no
    # way to tell them apart. set_index carries the pairing through to the workbook so a
    # product line stays attached to the invoice it was billed on.
    job_document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("job_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    set_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_field_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=True, index=True
    )
    label_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 1-based line-item row this value came from; NULL for ordinary single-value fields.
    # Values sharing a row_index within a job come from the same physical table row, which
    # is what lets an ICEGATE ITEMS sheet be assembled correctly.
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # is_target_value fields only: what the field actually produced BEFORE a reference-table
    # lookup replaced it (a match) or blanked it (no match) - extracted_value shows the
    # looked-up outcome the operator should see, this preserves the original key so a later
    # correction can still be learned against the right value (see remember_reference).
    target_value_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Where THIS value's own text was actually found on the real uploaded document at
    # extraction time (normalized 0-1, same scale as FieldMark.x/y/width/height) — a sliding-
    # window match of extracted_value against that document's own OCR word boxes (see
    # app.core.docai.locate_value_bbox). Deliberately separate from FieldMark's static
    # template-drawn box: every real document has its own layout, so the template's one-time
    # box is only right by coincidence. None when no confident match was found (a
    # reformatted value, a computed/custom field, a page read by vision fallback with no
    # OCR tokens) — the frontend shows no highlight rather than one that might be wrong.
    found_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    found_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    found_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    found_height: Mapped[float | None] = mapped_column(Float, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="field_values")

    @property
    def value(self) -> str | None:
        return self.corrected_value if self.corrected_value is not None else self.extracted_value
