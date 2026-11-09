from datetime import datetime

from pydantic import BaseModel, ConfigDict


from pydantic import EmailStr


class TenantCreate(BaseModel):
    name: str
    region: str | None = None
    currency: str | None = None
    # Which transport modes (app/core/modes.py) this client is licensed for. Empty/omitted =
    # unrestricted — every template creation offers all of them, same as before this existed.
    allowed_modes: list[str] | None = None
    # Optional: create the tenant's admin login in the same step.
    admin_full_name: str | None = None
    admin_email: EmailStr | None = None
    admin_password: str | None = None


class TenantUpdate(BaseModel):
    # A Tenant Admin calls this from their own "Shipment Type" page to manage which modes
    # THEIR tenant is licensed for; a Super Admin can also use it for the same tenant-level
    # field. Empty list = unrestricted, same meaning as at creation.
    allowed_modes: list[str] | None = None
    # Set from the "Masters" page: {"operator": bool, "gk2": bool} — False locks that role's
    # users in this tenant to read-only. A key not being present leaves that role's current
    # setting unchanged (a bare {} or omitting the field entirely is a no-op, not a reset).
    role_write_enabled: dict[str, bool] | None = None
    # Set from the "Masters" page: {"Sea Import": bool, ...}, keyed by MODES. False for a mode
    # means a job of that shipment type skips GK2 review entirely once GK1 approves it - same
    # merge-not-replace contract as role_write_enabled.
    gk2_required_by_mode: dict[str, bool] | None = None


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    region: str | None
    currency: str | None
    is_active: bool
    allowed_modes: list[str] | None = None
    role_write_enabled: dict[str, bool] | None = None
    gk2_required_by_mode: dict[str, bool] | None = None
    irn_pending_access_key: str | None = None


TRACKCONTAINER_PLANS = ("starter", "professional")


class TenantBillingConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cargora_monthly_fee: float
    cargora_tier1_rate_per_page: float
    cargora_tier1_page_limit: int
    cargora_tier2_rate_per_page: float
    trackcontainer_plan: str | None
    trackcontainer_starter_price: float
    trackcontainer_starter_container_limit: int
    trackcontainer_professional_price: float
    trackcontainer_professional_container_limit: int
    gst_percent: float
    billing_start_date: str | None


class TenantBillingConfigUpdate(BaseModel):
    # All optional — only provided fields are changed. A field left out keeps its current
    # (or default) value rather than being reset, so editing one number never clobbers the rest.
    cargora_monthly_fee: float | None = None
    cargora_tier1_rate_per_page: float | None = None
    cargora_tier1_page_limit: int | None = None
    cargora_tier2_rate_per_page: float | None = None
    # null = not subscribed to TrackContainer at all.
    trackcontainer_plan: str | None = None
    trackcontainer_starter_price: float | None = None
    trackcontainer_starter_container_limit: int | None = None
    trackcontainer_professional_price: float | None = None
    trackcontainer_professional_container_limit: int | None = None
    gst_percent: float | None = None
    # YYYY-MM-DD, or null to clear it back to "no proration". See the model's own docstring.
    billing_start_date: str | None = None


class TenantExtractionConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    extraction_paused: bool
    openai_key_mode: str


class TenantExtractionConfigUpdate(BaseModel):
    extraction_paused: bool | None = None
    openai_key_mode: str | None = None


class TenantOpenAIKeyOut(BaseModel):
    id: str
    label: str
    is_active: bool
    sort_order: int


class TenantOpenAIKeyIn(BaseModel):
    label: str
    api_key: str


class TenantOpenAIKeyActiveIn(BaseModel):
    is_active: bool


class ShipmentTypeUsageOut(BaseModel):
    shipment_type: str
    jobs: int
    pages: int


class BillingUsageOut(BaseModel):
    """Usage + bill breakdown for one calendar month. is_finalized tells the frontend whether
    this is the live, still-accumulating current month or a permanent, already-raised invoice's
    own numbers (in which case invoice_id/invoice_number/status are also set)."""
    month: str  # YYYY-MM
    is_finalized: bool
    invoice_id: str | None = None
    invoice_number: str | None = None
    status: str | None = None

    total_jobs: int
    total_pages: int
    by_shipment_type: list[ShipmentTypeUsageOut]

    # Of total_pages: this job's own first-time processing vs an explicit reclassify-triggered
    # re-run - see BillablePageEvent.is_rerun's own docstring. None on a finalized (past-month,
    # read from a stored Invoice) response - that breakdown was never recorded on older
    # invoices, only on the live ledger this field is computed from.
    pages_original: int | None = None
    pages_rerun: int | None = None
    # Read live from current job/document state, not the permanent ledger - see
    # UsageResult.final_classified_pages' own docstring for why these can drift slightly from
    # pages_original. None on a finalized response, same reason as above.
    final_classified_pages: int | None = None
    final_unclassified_pages: int | None = None
    # Of the jobs billed this period, how many still exist vs have been hard-deleted since -
    # a different count from total_jobs above (scoped by billing-event date, not job creation
    # date). None on a finalized response, same reason as above.
    active_jobs_billed: int | None = None
    deleted_jobs_billed: int | None = None
    # pages_original minus whatever is still classified/unclassified live - see
    # JobBillingRowOut.deleted_pages' own docstring for the same idea per job. None on a
    # finalized response, same reason as above.
    deleted_pages: int | None = None

    platform_fee: float
    platform_fee_billed_days: int
    platform_fee_month_days: int
    tier1_rate: float
    tier1_pages: int
    tier1_amount: float
    tier2_rate: float
    tier2_pages: int
    tier2_amount: float
    usage_charges: float
    subtotal: float
    gst_percent: float
    gst_amount: float
    total_amount: float


class JobBillingRowOut(BaseModel):
    """One row of the Billing & Usage page's own per-job table - see
    app.core.billing.JobBillingRow's own docstring for exactly what each field means and where
    it comes from (the permanent ledger vs the job's own current live state)."""
    job_id: str
    job_reference: str
    shipment_type: str
    status: str
    started_at: str  # ISO 8601
    original_pages: int
    rerun_pages: int
    final_classified_pages: int
    final_unclassified_pages: int
    deleted_pages: int  # original_pages minus whatever is still classified/unclassified live
    total_billed: int  # original_pages + rerun_pages


class JobBillingPageOut(BaseModel):
    items: list[JobBillingRowOut]
    total: int
    page: int
    page_size: int


class DocTypeAccuracyOut(BaseModel):
    document_name: str
    documents: int
    fields_extracted: int
    fields_edited: int
    accuracy_pct: float | None


class AccuracyTrendPointOut(BaseModel):
    day: str
    accuracy_pct: float | None


class AccuracyDashboardOut(BaseModel):
    documents_processed: int
    fields_extracted_by_ai: int
    fields_manually_edited: int
    accuracy_pct: float | None
    by_document_type: list[DocTypeAccuracyOut]
    trend: list[AccuracyTrendPointOut]


class ProcessingTimeTrendPointOut(BaseModel):
    day: str
    avg_total_tat_minutes: float | None


class ProcessingTimeDashboardOut(BaseModel):
    total_jobs: int
    avg_total_tat_minutes: float | None
    avg_extraction_minutes: float | None
    avg_review_minutes: float | None
    avg_erp_submission_minutes: float | None
    trend: list[ProcessingTimeTrendPointOut]


class EmployeeReviewRowOut(BaseModel):
    user_id: str
    name: str
    jobs_reviewed: int
    review_hours: float
    avg_minutes_per_job: float | None


class WorkforceDashboardOut(BaseModel):
    employee_requirement_fte: float | None
    avg_review_minutes_per_job: float | None
    total_jobs: int
    human_review_hours: float
    jobs_with_recorded_review: int
    by_employee: list[EmployeeReviewRowOut]


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_number: str
    period_start: str
    period_end: str
    platform_fee: float
    platform_fee_billed_days: int
    platform_fee_month_days: int
    total_jobs: int
    total_pages: int
    tier1_rate: float
    tier1_pages: int
    tier1_amount: float
    tier2_rate: float
    tier2_pages: int
    tier2_amount: float
    usage_charges: float
    subtotal: float
    gst_percent: float
    gst_amount: float
    total_amount: float
    by_shipment_type: dict[str, dict[str, int]]
    status: str
    paid_at: str | None
    created_at: datetime


class MarkInvoicePaid(BaseModel):
    paid: bool = True
