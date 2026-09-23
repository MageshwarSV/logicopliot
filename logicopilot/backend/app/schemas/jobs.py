from datetime import datetime

from pydantic import BaseModel, ConfigDict


class JobCreate(BaseModel):
    group_id: str
    reference: str | None = None  # auto-generated Job No when omitted


class DuplicateDecision(BaseModel):
    # "approve": a genuinely separate shipment - let it proceed as if never flagged.
    # "hold": no backend call needed at all — dismiss the popup client-side, the job stays
    # "possible_duplicate" for whenever someone comes back to it.
    # "delete": use the existing DELETE /jobs/{job_id} — no separate action here.
    decision: str  # "approve"


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    group_id: str
    reference: str
    status: str
    stage: str | None = None  # Documents | Verification | ERP Entry | Completed
    # The single word for the job list and header — coarser than `stage`, and the one that
    # should be shown to an operator. See _outer_status in api/v1/jobs.py for what maps to what.
    outer_status: str | None = None
    # Whose shipment this is, read off the job's own documents. The list column is headed
    # "Customer name" and was showing the TEMPLATE's name, which is a different thing: every
    # nokia(excel) job showed "nokia(excel)" whoever the consignee turned out to be.
    customer_name: str | None = None
    # How the shipment travelled and which way — "Sea Import", "Air Export". Worked out from
    # the job's own values, so it is empty when the documents do not say rather than guessed
    # from the template's name.
    mode: str | None = None
    assigned_operator_id: str | None = None
    # Whoever created the job (a logged-in Operator), or failing that whoever a mail pull
    # routed it to — the list column is for Admin/Super Admin only, so they can see which
    # operator a job actually belongs to without opening it.
    operator_name: str | None = None
    # The operator's list shows when a job last moved, so these have to leave the API. They
    # exist on the row already - they simply were never returned.
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Set when status is "possible_duplicate" - the job whose extracted data this one's
    # checksum matched. duplicate_of_reference is the human-readable Job No for the popup
    # ("this looks like JOB-1E928B") - the id alone means nothing to an operator.
    duplicate_of_job_id: str | None = None
    duplicate_of_reference: str | None = None
    # GK1-set target date ("YYYY-MM-DD"), edited from the Jobs list - drives the ETA boxes on
    # the operator dashboard. None = no target date set.
    eta_date: str | None = None


class JobEventOut(BaseModel):
    """One line of a job's history, for the popup behind the Last updated column."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    # What to CALL that status. The screen had its own copy of this mapping and the two had
    # already drifted: the popup's heading said "Extracting" while the line underneath it said
    # "-> Extracting the documents", for the same event. One set of words, sent with the event.
    label: str | None = None
    stage: str | None = None
    note: str | None = None
    at: datetime


class JobDocumentOut(BaseModel):
    id: str
    template_document_id: str
    name: str
    doc_type: str
    is_uploaded: bool
    page_count: int
    # A slot can hold several files. file_index orders them; set_index says which invoice
    # this one was paired into (None = it covers the whole job, e.g. one bill of lading for
    # all three invoices); original_name is what the operator recognises it by.
    file_index: int = 0
    set_index: int | None = None
    original_name: str | None = None
    # The raw OCR this document was read from, per page. Stored since the "save the extracted
    # OCR" change but never returned, which left no way to tell a genuine extraction fault
    # from the document simply having been read badly.
    extracted_json: dict | None = None
    # Operator (GK1) pressed "Submit for Approval" on this document, on Data Extraction.
    approved: bool = False
    # GK2's OWN independent approval of this document — see JobDocument.gk2_approved.
    gk2_approved: bool = False
    # False = this document is optional — Document Capture can complete without it.
    is_required: bool = True


class JobFieldValueOut(BaseModel):
    id: str
    mark_id: str | None = None
    template_document_id: str | None = None
    document_name: str
    label_name: str
    extracted_value: str | None
    corrected_value: str | None
    value: str | None
    is_custom: bool = False
    # Mirrors FieldMark.ask_operator so the operator page knows what to prompt for.
    ask_operator: bool = False
    # Guidance for the operator, e.g. "T for Transaction, D for Deferred".
    ask_operator_required: bool = True  # False = optional, never blocks Submit Entry
    ask_operator_hint: str | None = None
    # This field answers itself — a lookup from the customer's own reference sheet. The submit
    # gate accepts its value without a typed confirmation, and the screen must apply the SAME
    # rule or the button and the server disagree about whether the job can go. Everything else
    # marked "ask the operator" needs the operator to actually confirm it, however it was
    # pre-filled — which is the point of pre-filling a value that changes each year.
    self_filled: bool = False
    # WHICH uploaded file this value was read from, and which invoice-set that file belongs
    # to. template_document_id only names the slot, so with three invoices on one job the
    # extraction screen could not tell their values apart to show them beside the right page.
    job_document_id: str | None = None
    set_index: int | None = None
    # For a COMPUTED field: the documents it was told to read. A computed value has no mark
    # and no document of its own, so without this it belonged to no screen and was shown
    # nowhere — a dozen of nokia's fields were invisible. Empty list = every document.
    source_document_ids: list[str] = []
    # "document" | "reference" | "computed" | "fixed" — what produced this value.
    origin: str = "document"
    # 1-based table row this value came from; None for ordinary fields.
    row_index: int | None = None
    # Where this value's mark sits on its own document, straight off FieldMark - lets Data
    # Extraction highlight/scroll to the exact spot on the page preview when this field is
    # focused. None when there's no mark (custom/computed field); a mark saved as a
    # zero-area "no region" sentinel (see the cross-document-copy path in field_marks.py)
    # still reports mark_page but width/height of 0, so the frontend knows to skip drawing.
    mark_page: int | None = None
    mark_x: float | None = None
    mark_y: float | None = None
    mark_width: float | None = None
    mark_height: float | None = None
    # Where this value's own text was actually FOUND on this real uploaded document (a
    # sliding-window OCR-token match, see app.core.docai.locate_value_bbox) - this, not
    # mark_*, is what Data Extraction should highlight against. Every real document has its
    # own layout, so FieldMark's one-time template box is only right by coincidence; this is
    # per-document. None when extraction ran on a vision-fallback page (no OCR at all) or
    # found no confident text match - the frontend shows no highlight rather than guess.
    found_page: int | None = None
    found_x: float | None = None
    found_y: float | None = None
    found_width: float | None = None
    found_height: float | None = None


class VerificationRow(BaseModel):
    link_id: str
    field_label: str
    source_document: str
    source_value: str | None
    target_document: str
    target_value: str | None
    status: str  # match | mismatch | missing | review
    accepted: bool = False


class VerificationDecision(BaseModel):
    link_id: str
    accept: bool  # True = accept this row, False = un-accept


class JobDetailOut(BaseModel):
    id: str
    tenant_id: str
    group_id: str
    group_name: str
    reference: str
    status: str
    stage: str | None = None
    outer_status: str | None = None
    mode: str | None = None
    # Same meaning as on JobOut - who this job is assigned to, and their name, so the Jobs
    # list's assign dropdown reflects a change immediately without waiting for its own
    # separate list refresh to land.
    assigned_operator_id: str | None = None
    operator_name: str | None = None
    # Whether this job's template submits via a bulk Excel import (TemplateGroup.entry_mode)
    # rather than typed-field browser automation - lets the screen offer "Download as Excel"
    # only where there is a workbook to download.
    excel_entry: bool = False
    # The raw GK2 sign-off state (see Job.gk2_status: None, pending, preparing_erp,
    # entering_erp, submitted, failed) - outer_status turns this into a display word, but the
    # screen needs the raw value too: "Failed" reads identically whether it came from GK2's own
    # run or from an ordinary GK1 entry that never went near GK2, and only this field tells
    # those apart (GK1's side must not show "waiting on GK2" for a job GK2 never saw).
    gk2_status: str | None = None
    # Operator (GK1) pressed "Approved and Proceed" on Data Validation, for the current
    # extraction.
    validation_approved: bool = False
    # GK2's OWN independent approval of Data Validation — see Job.gk2_validation_approved.
    gk2_validation_approved: bool = False
    # GK1's IRN Documents Upload stage - see Job.irn_documents_done.
    irn_documents_done: bool = False
    # See JobOut - same meaning, for the single-job screen's own duplicate popup.
    duplicate_of_job_id: str | None = None
    duplicate_of_reference: str | None = None
    eta_date: str | None = None
    documents: list[JobDocumentOut]
    field_values: list[JobFieldValueOut]
    verifications: list[VerificationRow]
    all_checks_passed: bool
    # Assembled {document -> {label: value}} snapshot, mirroring jobs.extracted_keyouted_data.
    extracted_keyouted_data: dict | None = None
    # Filled in by Submit Entry: outcome of replaying the ERP script.
    erp_status: str | None = None  # ok | error | no_script | failed
    erp_final_url: str | None = None
    erp_log: list[str] | None = None
    erp_screenshot: str | None = None  # base64 PNG proof of the final ERP page
    erp_reason: str | None = None
    erp_failed_field: str | None = None  # data field (label_name) whose value the ERP rejected
    erp_failed_value: str | None = None  # the value that wasn't found in the ERP
    # Values read back OUT of the ERP — the reference it generated (Bill of Entry number,
    # acknowledgement). Persisted on the job, so it survives past the response.
    erp_captured: dict | None = None
    # What the AI saw on the screen where the run stopped, in plain words for the operator.
    erp_diagnosis: str | None = None


class FieldValueCorrect(BaseModel):
    corrected_value: str


class AvailableGroup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    status: str
    # Which transport mode this template is for (app/core/modes.py) — set at creation, or
    # afterwards via PATCH /template-groups/{id}/mode.
    mode: str | None = None
    pull_email: str | None = None
    pull_operator_id: str | None = None
