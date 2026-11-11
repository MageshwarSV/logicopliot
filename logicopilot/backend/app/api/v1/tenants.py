import csv
import io
import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.billing import (
    HIGHLIGHTS, calculate_bill, compute_usage, default_billing_config, ensure_invoices_finalized,
    list_job_billing, list_job_billing_export,
)
from app.core.dashboard_metrics import (
    compute_accuracy_metrics, compute_processing_time_metrics, compute_workforce_metrics,
)
from app.core.deps import get_current_user, get_db, require_role
from app.core.finance_aggregation import summarize_tenant_bill, trailing_months
from app.core.finance_export import rows_to_csv, rows_to_xlsx
from app.core.modes import MODES
from app.core.openai_pool import (
    add_api_key,
    delete_api_key,
    list_api_keys,
    refresh_tenant_modes,
    set_api_key_active,
)
from app.core.security import hash_password
from app.core.system_settings import InvalidOpenAIKey
from app.models.invoice import Invoice
from app.models.job import Job
from app.models.openai_api_key import OpenAIApiKey
from app.models.tenant import Tenant
from app.models.tenant_billing_config import TenantBillingConfig
from app.models.tenant_extraction_config import TenantExtractionConfig
from app.models.user import ADMIN, GK2, OPERATOR, SAAS_FINANCE, SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.tenant import (
    TRACKCONTAINER_PLANS,
    AccuracyDashboardOut,
    AccuracyTrendPointOut,
    BillingUsageOut,
    DocTypeAccuracyOut,
    EmployeeReviewRowOut,
    InvoiceOut,
    JobAccuracyOut,
    JobBillingPageOut,
    JobBillingRowOut,
    JobTimingOut,
    MarkInvoicePaid,
    ProcessingTimeDashboardOut,
    ProcessingTimeTrendPointOut,
    ShipmentTypeUsageOut,
    TenantBillingConfigOut,
    WorkforceDashboardOut,
    TenantBillingConfigUpdate,
    TenantCreate,
    TenantExtractionConfigOut,
    TenantExtractionConfigUpdate,
    TenantOpenAIKeyActiveIn,
    TenantOpenAIKeyIn,
    TenantOpenAIKeyOut,
    TenantOut,
    TenantUpdate,
)

VALID_OPENAI_KEY_MODES = ("shared", "own")

WRITE_TOGGLE_ROLES = (OPERATOR, GK2)

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    creator: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantOut:
    """Creates the company and, if admin credentials are supplied, its tenant-admin
    login in the same step (atomic — a bad email rolls the whole thing back)."""
    wants_admin = any([payload.admin_full_name, payload.admin_email, payload.admin_password])
    if wants_admin and not all([payload.admin_full_name, payload.admin_email, payload.admin_password]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide admin name, email, and password together (or none).",
        )
    if wants_admin and len(payload.admin_password or "") < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admin password must be at least 8 characters.")

    allowed_modes = None
    if payload.allowed_modes:
        allowed_modes = sorted(set(payload.allowed_modes))
        bad = [m for m in allowed_modes if m not in MODES]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a valid mode: {', '.join(bad)}",
            )

    tenant = Tenant(name=payload.name, region=payload.region, currency=payload.currency,
                    allowed_modes=allowed_modes)
    db.add(tenant)
    try:
        db.flush()  # assign tenant.id; still one transaction with the admin below
        if wants_admin:
            db.add(
                User(
                    email=payload.admin_email,
                    hashed_password=hash_password(payload.admin_password),
                    full_name=payload.admin_full_name,
                    role=TENANT_ADMIN,
                    tenant_id=tenant.id,
                    created_by_id=creator.id,
                    # Super Admin chose this password, not the tenant admin themselves - same
                    # forced first-login change as create_user's own path.
                    must_change_password=True,
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with this name, or a user with that admin email, already exists.",
        )
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.get("", response_model=list[TenantOut])
def list_tenants(
    db: Session = Depends(get_db),
    # ADMIN needs this too: the jobs list's tenant-filter dropdown is built from it.
    # SAAS_FINANCE needs the full tenant list for its own Customers page.
    _=Depends(require_role(SUPER_ADMIN, ADMIN, SAAS_FINANCE)),
) -> list[TenantOut]:
    tenants = db.query(Tenant).all()
    return [TenantOut.model_validate(t) for t in tenants]


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantOut:
    """Super admins and SaaS Finance can look up any tenant; everyone else only their own
    (404 otherwise) — this is how a Tenant Admin/Operator dashboard shows its own tenant's
    name, and how Finance's customer-detail page resolves any tenant by id."""
    if user.role not in (SUPER_ADMIN, SAAS_FINANCE) and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return TenantOut.model_validate(tenant)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantOut:
    """Super Admin may edit any tenant; a Tenant Admin only their own (404 otherwise, same
    as get_tenant) — this is how the Tenant Admin's own "Shipment Type" page manages which
    modes THEIR tenant is licensed for, without needing Super Admin access."""
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if payload.allowed_modes is not None:
        allowed_modes = sorted(set(payload.allowed_modes)) if payload.allowed_modes else None
        bad = [m for m in (allowed_modes or []) if m not in MODES]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a valid mode: {', '.join(bad)}",
            )
        tenant.allowed_modes = allowed_modes

    if payload.role_write_enabled is not None:
        bad_roles = [r for r in payload.role_write_enabled if r not in WRITE_TOGGLE_ROLES]
        if bad_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a role this can be set for: {', '.join(bad_roles)}",
            )
        # Merge, not replace — the Masters page edits one role type at a time and must not
        # silently reset the other one's setting.
        merged = dict(tenant.role_write_enabled or {})
        merged.update(payload.role_write_enabled)
        tenant.role_write_enabled = merged

    if payload.gk2_required_by_mode is not None:
        bad_modes = [m for m in payload.gk2_required_by_mode if m not in MODES]
        if bad_modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a valid mode: {', '.join(bad_modes)}",
            )
        # Merge, not replace — editing one shipment type must not reset another's setting.
        merged = dict(tenant.gk2_required_by_mode or {})
        merged.update(payload.gk2_required_by_mode)
        tenant.gk2_required_by_mode = merged

    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.get("/{tenant_id}/billing-config", response_model=TenantBillingConfigOut)
def get_tenant_billing_config(
    tenant_id: str,
    db: Session = Depends(get_db),
    # SaaS Finance may VIEW pricing (its own Pricing & Plans page), and a Tenant Admin may
    # VIEW their own tenant's plan (the Subscription & Plans page) - update_tenant_billing_config
    # below stays Super-Admin-only either way; nobody self-serves a rate change here.
    user: User = Depends(require_role(SUPER_ADMIN, SAAS_FINANCE, TENANT_ADMIN)),
) -> TenantBillingConfigOut:
    """Pure reference data for the Super Admin's own records - what this tenant is billed for
    CARGORA itself and the TrackContainer.in integration. Returns the model's own defaults
    (the standard published rates) when nothing has been saved for this tenant yet, rather
    than 404ing - there is nothing wrong with a tenant nobody has configured this for."""
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    config = db.get(TenantBillingConfig, tenant_id)
    if config is not None:
        return TenantBillingConfigOut.model_validate(config)
    # No row saved yet - the model's own `default=` only ever applies at INSERT time, so a
    # transient, never-flushed instance has plain None on every column; the standard published
    # rates are spelled out again here instead; keep in sync with TenantBillingConfig's own
    # column defaults.
    return TenantBillingConfigOut(
        cargora_monthly_fee=4999.0, cargora_tier1_rate_per_page=2.50, cargora_tier1_page_limit=25000,
        cargora_tier2_rate_per_page=2.00, trackcontainer_plan=None, trackcontainer_starter_price=2999.0,
        trackcontainer_starter_container_limit=500, trackcontainer_professional_price=5999.0,
        trackcontainer_professional_container_limit=1500, gst_percent=18.0, billing_start_date=None,
    )


@router.put("/{tenant_id}/billing-config", response_model=TenantBillingConfigOut)
def update_tenant_billing_config(
    tenant_id: str,
    payload: TenantBillingConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantBillingConfigOut:
    """Creates the row on first save (a tenant has none until a Super Admin actually sets
    something) or updates the existing one - only the fields sent are changed, same
    leave-the-rest-alone contract every other partial update in this codebase uses."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if payload.trackcontainer_plan is not None and payload.trackcontainer_plan not in TRACKCONTAINER_PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"trackcontainer_plan must be one of {TRACKCONTAINER_PLANS}",
        )
    if payload.billing_start_date is not None:
        try:
            date.fromisoformat(payload.billing_start_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="billing_start_date must be YYYY-MM-DD"
            )
    config = db.get(TenantBillingConfig, tenant_id)
    if config is None:
        config = TenantBillingConfig(tenant_id=tenant_id)
        db.add(config)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    db.commit()
    db.refresh(config)
    return TenantBillingConfigOut.model_validate(config)


@router.get("/{tenant_id}/extraction-config", response_model=TenantExtractionConfigOut)
def get_tenant_extraction_config(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantExtractionConfigOut:
    """This tenant's own extraction pause + OpenAI key routing, independent of every other
    tenant and of the platform-wide switches on Settings. Returns the built-in defaults
    (not paused, shared pool) when nothing has been saved for this tenant yet - no row is
    not an error, see TenantExtractionConfig's own docstring."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    config = db.get(TenantExtractionConfig, tenant_id)
    if config is not None:
        return TenantExtractionConfigOut.model_validate(config)
    return TenantExtractionConfigOut(extraction_paused=False, openai_key_mode="shared")


@router.put("/{tenant_id}/extraction-config", response_model=TenantExtractionConfigOut)
def update_tenant_extraction_config(
    tenant_id: str,
    payload: TenantExtractionConfigUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantExtractionConfigOut:
    """Creates the row on first save, same lazy-create contract as billing-config. Pausing or
    resuming here, or switching key mode, takes effect on this tenant's very next job - no
    restart needed."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if payload.openai_key_mode is not None and payload.openai_key_mode not in VALID_OPENAI_KEY_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"openai_key_mode must be one of {VALID_OPENAI_KEY_MODES}",
        )
    config = db.get(TenantExtractionConfig, tenant_id)
    if config is None:
        config = TenantExtractionConfig(tenant_id=tenant_id)
        db.add(config)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    db.commit()
    db.refresh(config)
    if payload.openai_key_mode is not None:
        refresh_tenant_modes(db)
    return TenantExtractionConfigOut.model_validate(config)


def _tenant_pool_key_out(k) -> TenantOpenAIKeyOut:
    return TenantOpenAIKeyOut(id=k.id, label=k.label, is_active=k.is_active, sort_order=k.sort_order)


@router.get("/{tenant_id}/openai-key-pool", response_model=list[TenantOpenAIKeyOut])
def list_tenant_openai_key_pool(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> list[TenantOpenAIKeyOut]:
    """This tenant's own pooled keys only - never the global pool's, and never any other
    tenant's. Used only while that tenant's openai_key_mode is "own" (see
    app/core/openai_pool.py); the rows may still exist and simply go unused while on
    "shared" mode, same as the global pool's own empty-pool fallback."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return [_tenant_pool_key_out(k) for k in list_api_keys(db, tenant_id=tenant_id)]


@router.post("/{tenant_id}/openai-key-pool", status_code=status.HTTP_201_CREATED,
             response_model=TenantOpenAIKeyOut)
def add_tenant_openai_key_pool_key(
    tenant_id: str,
    payload: TenantOpenAIKeyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantOpenAIKeyOut:
    """Verified live against OpenAI itself before saving, same as the global pool. Takes
    effect immediately once this tenant is in "own" key mode."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    try:
        row = add_api_key(db, payload.label, payload.api_key, tenant_id=tenant_id)
    except InvalidOpenAIKey as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"OpenAI rejected this key: {exc}")
    return _tenant_pool_key_out(row)


@router.patch("/{tenant_id}/openai-key-pool/{key_id}", response_model=TenantOpenAIKeyOut)
def set_tenant_openai_key_pool_key_active(
    tenant_id: str,
    key_id: str,
    payload: TenantOpenAIKeyActiveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantOpenAIKeyOut:
    row = set_api_key_active(db, key_id, payload.is_active)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such key")
    return _tenant_pool_key_out(row)


@router.delete("/{tenant_id}/openai-key-pool/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant_openai_key_pool_key(
    tenant_id: str,
    key_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> None:
    row = db.get(__import__("app.models.openai_api_key", fromlist=["OpenAIApiKey"]).OpenAIApiKey, key_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such key")
    delete_api_key(db, key_id)


def _require_billing_read_access(user: User, tenant_id: str) -> None:
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN, SAAS_FINANCE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")


@router.get("/{tenant_id}/billing/usage", response_model=BillingUsageOut)
def get_billing_usage(
    tenant_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BillingUsageOut:
    """The Billing & Usage page's headline figure for one calendar month (default: the
    current one). The current, still-running month is always computed live from real job/
    document rows and is never finalized into an Invoice; any earlier month is looked up from
    its permanent, already-raised Invoice row (raised lazily by ensure_invoices_finalized,
    called here on every access since there is no scheduler process in this codebase).
    Super Admin may view any tenant; a Tenant Admin only their own."""
    _require_billing_read_access(user, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    today = datetime.now(timezone.utc).date()
    if month:
        try:
            period_start = date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="month must be YYYY-MM")
    else:
        period_start = today.replace(day=1)

    ensure_invoices_finalized(db, tenant_id)

    if period_start < today.replace(day=1):
        invoice = (
            db.query(Invoice)
            .filter(Invoice.tenant_id == tenant_id, Invoice.period_start == period_start.isoformat())
            .first()
        )
        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No usage recorded for this period"
            )
        return BillingUsageOut(
            month=period_start.strftime("%Y-%m"), is_finalized=True,
            invoice_id=invoice.id, invoice_number=invoice.invoice_number, status=invoice.status,
            total_jobs=invoice.total_jobs, total_pages=invoice.total_pages,
            by_shipment_type=[
                ShipmentTypeUsageOut(shipment_type=k, jobs=v["jobs"], pages=v["pages"])
                for k, v in sorted(invoice.by_shipment_type.items())
            ],
            platform_fee=invoice.platform_fee,
            platform_fee_billed_days=invoice.platform_fee_billed_days,
            platform_fee_month_days=invoice.platform_fee_month_days,
            tier1_rate=invoice.tier1_rate, tier1_pages=invoice.tier1_pages, tier1_amount=invoice.tier1_amount,
            tier2_rate=invoice.tier2_rate, tier2_pages=invoice.tier2_pages, tier2_amount=invoice.tier2_amount,
            not_required_rate=invoice.not_required_rate, not_required_pages=invoice.not_required_pages,
            not_required_amount=invoice.not_required_amount,
            usage_charges=invoice.usage_charges, subtotal=invoice.subtotal,
            gst_percent=invoice.gst_percent, gst_amount=invoice.gst_amount, total_amount=invoice.total_amount,
        )

    config = db.get(TenantBillingConfig, tenant_id) or default_billing_config(tenant_id)
    usage = compute_usage(db, tenant_id, period_start)
    bill = calculate_bill(config, usage.total_pages, period_start, usage.final_unclassified_pages)
    return BillingUsageOut(
        month=period_start.strftime("%Y-%m"), is_finalized=False,
        total_jobs=usage.total_jobs, total_pages=usage.total_pages,
        by_shipment_type=[
            ShipmentTypeUsageOut(shipment_type=k, jobs=v["jobs"], pages=v["pages"])
            for k, v in sorted(usage.by_shipment_type.items())
        ],
        pages_original=usage.pages_original, pages_rerun=usage.pages_rerun,
        final_classified_pages=usage.final_classified_pages,
        final_unclassified_pages=usage.final_unclassified_pages,
        active_jobs_billed=usage.active_jobs_billed,
        deleted_jobs_billed=usage.deleted_jobs_billed,
        deleted_pages=usage.deleted_pages,
        platform_fee=bill.platform_fee,
        platform_fee_billed_days=bill.platform_fee_billed_days,
        platform_fee_month_days=bill.platform_fee_month_days,
        tier1_rate=bill.tier1_rate, tier1_pages=bill.tier1_pages,
        tier1_amount=bill.tier1_amount, tier2_rate=bill.tier2_rate, tier2_pages=bill.tier2_pages,
        tier2_amount=bill.tier2_amount,
        not_required_rate=bill.not_required_rate, not_required_pages=bill.not_required_pages,
        not_required_amount=bill.not_required_amount,
        usage_charges=bill.usage_charges, subtotal=bill.subtotal,
        gst_percent=bill.gst_percent, gst_amount=bill.gst_amount, total_amount=bill.total_amount,
    )


def _parse_period_start(month: str | None) -> date:
    if month:
        try:
            return date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="month must be YYYY-MM")
    return datetime.now(timezone.utc).date().replace(day=1)


def _parse_optional_date(value: str | None, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be YYYY-MM-DD")


@router.get("/{tenant_id}/billing/jobs", response_model=JobBillingPageOut)
def list_billing_jobs(
    tenant_id: str,
    month: str | None = None,
    search: str | None = None,
    shipment_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    highlight: str | None = None,
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobBillingPageOut:
    """The Billing & Usage page's own per-job table - one row per job billed in the given month
    (by event date, same scoping as get_billing_usage), whether or not that job still exists.
    date_from/date_to (YYYY-MM-DD) further narrow by each job's own earliest billing event this
    period - a separate, finer-grained filter from `month`, not a replacement for it. `highlight`
    (one of billing.HIGHLIGHTS) narrows further to just the jobs behind one of the page's own
    StatCards - clicking Deleted Pages, Rerun Pages, Not Required Pages or Deleted Jobs. Only
    ever reads the live current month's state - there is no per-job breakdown stored on a
    finalized Invoice, so this always reflects "right now," even when `month` names an already-
    finalized past period. Super Admin may view any tenant; a Tenant Admin only their own."""
    _require_billing_read_access(user, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if highlight is not None and highlight not in HIGHLIGHTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"highlight must be one of: {', '.join(HIGHLIGHTS)}",
        )

    period_start = _parse_period_start(month)
    from_date = _parse_optional_date(date_from, "date_from")
    to_date = _parse_optional_date(date_to, "date_to")

    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    rows, total = list_job_billing(
        db, tenant_id, period_start, search=search, shipment_type=shipment_type,
        date_from=from_date, date_to=to_date, highlight=highlight, page=page, page_size=page_size,
    )
    return JobBillingPageOut(
        items=[
            JobBillingRowOut(
                job_id=r.job_id, job_reference=r.job_reference, shipment_type=r.shipment_type,
                status=r.status, started_at=r.started_at.isoformat(),
                original_pages=r.original_pages, rerun_pages=r.rerun_pages,
                final_classified_pages=r.final_classified_pages,
                final_unclassified_pages=r.final_unclassified_pages,
                deleted_pages=r.deleted_pages,
                total_billed=r.original_pages + r.rerun_pages,
            )
            for r in rows
        ],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{tenant_id}/billing/jobs/export")
def export_billing_jobs(
    tenant_id: str,
    month: str | None = None,
    search: str | None = None,
    shipment_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    highlight: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """CSV download of the same rows list_billing_jobs shows, with the same filters and no
    pagination - the "Export" button on the Billing & Usage page's Job Details table."""
    _require_billing_read_access(user, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if highlight is not None and highlight not in HIGHLIGHTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"highlight must be one of: {', '.join(HIGHLIGHTS)}",
        )

    period_start = _parse_period_start(month)
    from_date = _parse_optional_date(date_from, "date_from")
    to_date = _parse_optional_date(date_to, "date_to")

    rows = list_job_billing_export(
        db, tenant_id, period_start, search=search, shipment_type=shipment_type,
        date_from=from_date, date_to=to_date, highlight=highlight,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Job Number", "Shipment Type", "Status", "Job Start Time",
        "Original Pages", "Rerun Pages", "Total Pages",
    ])
    for r in rows:
        writer.writerow([
            r.job_reference, r.shipment_type, r.status, r.started_at.isoformat(),
            r.original_pages, r.rerun_pages, r.original_pages + r.rerun_pages,
        ])
    buffer.seek(0)
    filename = f"billing-jobs-{tenant_id}-{period_start.strftime('%Y-%m')}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{tenant_id}/dashboard/earliest-completed-job")
def get_earliest_completed_job_date(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """The date of this tenant's own first-ever COMPLETED job - what the Executive Dashboard's
    "All time" quick filter actually means, instead of an arbitrary fixed floor. Same read-access
    rule as the dashboards themselves. None when the tenant has no completed job yet."""
    _require_billing_read_access(user, tenant_id)
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    earliest = (
        db.query(Job.completed_at)
        .filter(Job.tenant_id == tenant_id, Job.status == "completed", Job.completed_at.isnot(None))
        .order_by(Job.completed_at.asc())
        .first()
    )
    return {"date": earliest[0].date().isoformat() if earliest else None}


def _resolve_dashboard_range(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    """Defaults to the current calendar month so far, same shape as the billing page's own
    default. Both ends are plain dates (inclusive) - dashboard_metrics converts to IST-aware
    UTC bounds itself."""
    today = datetime.now(timezone.utc).date()
    start = _parse_optional_date(date_from, "date_from") or today.replace(day=1)
    end = _parse_optional_date(date_to, "date_to") or today
    if end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_to must not be before date_from")
    if (end - start).days > 366:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Range too wide - pick 366 days or fewer")
    return start, end


def _job_timings_out(rows: list) -> list[JobTimingOut]:
    return [
        JobTimingOut(
            job_id=r.job_id, reference=r.reference, mode=r.mode,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            extraction_minutes=r.extraction_minutes, review_minutes=r.review_minutes,
            erp_submission_minutes=r.erp_submission_minutes,
        )
        for r in rows
    ]


def _resolve_dashboard_modes(mode: str | None) -> list[str] | None:
    if not mode:
        return None
    chosen = [m.strip() for m in mode.split(",") if m.strip()]
    invalid = [m for m in chosen if m not in MODES]
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown shipment mode(s): {', '.join(invalid)}")
    return chosen or None


@router.get("/{tenant_id}/dashboard/accuracy", response_model=AccuracyDashboardOut)
def get_accuracy_dashboard(
    tenant_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AccuracyDashboardOut:
    """Data Accuracy page of the Tenant Admin Executive Dashboard. Same read-access rule as
    billing (Super Admin any tenant, Tenant Admin only their own, SaaS Finance any)."""
    _require_billing_read_access(user, tenant_id)
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    start, end = _resolve_dashboard_range(date_from, date_to)
    modes = _resolve_dashboard_modes(mode)
    m = compute_accuracy_metrics(db, tenant_id, start, end, modes)
    return AccuracyDashboardOut(
        documents_processed=m.documents_processed,
        fields_extracted_by_ai=m.fields_extracted_by_ai,
        fields_manually_edited=m.fields_manually_edited,
        accuracy_pct=m.accuracy_pct,
        jobs_completed=m.jobs_completed,
        by_document_type=[
            DocTypeAccuracyOut(
                document_name=d.document_name, documents=d.documents,
                fields_extracted=d.fields_extracted, fields_edited=d.fields_edited,
                accuracy_pct=d.accuracy_pct,
            )
            for d in m.by_document_type
        ],
        trend=[AccuracyTrendPointOut(day=p.day.isoformat(), accuracy_pct=p.accuracy_pct) for p in m.trend],
        by_job=[
            JobAccuracyOut(
                job_id=j.job_id, reference=j.reference, mode=j.mode,
                completed_at=j.completed_at.isoformat() if j.completed_at else None,
                fields_extracted=j.fields_extracted, fields_edited=j.fields_edited,
                accuracy_pct=j.accuracy_pct,
            )
            for j in m.by_job
        ],
    )


@router.get("/{tenant_id}/dashboard/processing-time", response_model=ProcessingTimeDashboardOut)
def get_processing_time_dashboard(
    tenant_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProcessingTimeDashboardOut:
    """Job Processing Time page of the Tenant Admin Executive Dashboard. Same read-access rule
    as billing."""
    _require_billing_read_access(user, tenant_id)
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    start, end = _resolve_dashboard_range(date_from, date_to)
    modes = _resolve_dashboard_modes(mode)
    m = compute_processing_time_metrics(db, tenant_id, start, end, modes)
    return ProcessingTimeDashboardOut(
        total_jobs=m.total_jobs,
        avg_total_tat_minutes=m.avg_total_tat_minutes,
        avg_extraction_minutes=m.avg_extraction_minutes,
        avg_review_minutes=m.avg_review_minutes,
        avg_erp_submission_minutes=m.avg_erp_submission_minutes,
        trend=[ProcessingTimeTrendPointOut(day=p.day.isoformat(), avg_total_tat_minutes=p.avg_total_tat_minutes) for p in m.trend],
        by_job=_job_timings_out(m.by_job),
    )


@router.get("/{tenant_id}/dashboard/workforce", response_model=WorkforceDashboardOut)
def get_workforce_dashboard(
    tenant_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorkforceDashboardOut:
    """Workforce Productivity page of the Tenant Admin Executive Dashboard. Only counts jobs
    with a real recorded "Approved and Proceed" press (see compute_workforce_metrics's own
    docstring) - jobs_with_recorded_review says how many of total_jobs that covers."""
    _require_billing_read_access(user, tenant_id)
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    start, end = _resolve_dashboard_range(date_from, date_to)
    modes = _resolve_dashboard_modes(mode)
    m = compute_workforce_metrics(db, tenant_id, start, end, modes)
    return WorkforceDashboardOut(
        employee_requirement_fte=m.employee_requirement_fte,
        avg_review_minutes_per_job=m.avg_review_minutes_per_job,
        total_jobs=m.total_jobs,
        human_review_hours=m.human_review_hours,
        jobs_with_recorded_review=m.jobs_with_recorded_review,
        by_employee=[
            EmployeeReviewRowOut(
                user_id=r.user_id, name=r.name, jobs_reviewed=r.jobs_reviewed,
                review_hours=r.review_hours, avg_minutes_per_job=r.avg_minutes_per_job,
            )
            for r in m.by_employee
        ],
        by_job=_job_timings_out(m.by_job),
    )


@router.get("/{tenant_id}/billing/invoices", response_model=list[InvoiceOut])
def list_invoices(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[InvoiceOut]:
    """Invoice history for one tenant, most recent first. Finalizes any past month that has
    ended with no invoice row yet before listing, same as get_billing_usage."""
    _require_billing_read_access(user, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    ensure_invoices_finalized(db, tenant_id)
    invoices = (
        db.query(Invoice)
        .filter(Invoice.tenant_id == tenant_id)
        .order_by(Invoice.period_start.desc())
        .all()
    )
    return [InvoiceOut.model_validate(inv) for inv in invoices]


TENANT_REPORT_TYPES = ("monthly-usage", "billing-summary", "revenue-trend", "outstanding-payments")

_TENANT_REPORT_HEADERS = {
    "monthly-usage": ["Month", "Jobs", "Pages"],
    "billing-summary": ["Month", "Jobs", "Pages", "Platform Fee", "Usage Charges", "GST", "Total Amount", "Invoice Status"],
    "revenue-trend": ["Month", "Total Amount"],
    "outstanding-payments": ["Invoice Number", "Period", "Total Amount", "Status"],
}


def _tenant_report_rows(db: Session, tenant: Tenant, report_type: str, period_start: date) -> list[dict]:
    """The same report shapes saas_finance.py's platform-wide reports use, scoped down to
    just this one tenant - reuses summarize_tenant_bill/trailing_months rather than filtering
    summarize_all_tenants's own full-platform loop down to one row, which would still cost a
    query over every tenant just to throw away all but one."""
    if report_type == "revenue-trend":
        rows = []
        for month_start in trailing_months(period_start, 12):
            s = summarize_tenant_bill(db, tenant, month_start)
            rows.append({"Month": month_start.strftime("%Y-%m"), "Total Amount": s.total_amount})
        return rows
    if report_type == "outstanding-payments":
        unpaid = (
            db.query(Invoice)
            .filter(Invoice.tenant_id == tenant.id, Invoice.status == "unpaid")
            .order_by(Invoice.period_start.desc())
            .all()
        )
        return [
            {"Invoice Number": inv.invoice_number, "Period": inv.period_start,
             "Total Amount": inv.total_amount, "Status": inv.status}
            for inv in unpaid
        ]
    if report_type == "monthly-usage":
        rows = []
        for month_start in trailing_months(period_start, 12):
            s = summarize_tenant_bill(db, tenant, month_start)
            rows.append({"Month": month_start.strftime("%Y-%m"), "Jobs": s.total_jobs, "Pages": s.total_pages})
        return rows
    if report_type == "billing-summary":
        rows = []
        for month_start in trailing_months(period_start, 12):
            s = summarize_tenant_bill(db, tenant, month_start)
            rows.append({
                "Month": month_start.strftime("%Y-%m"), "Jobs": s.total_jobs, "Pages": s.total_pages,
                "Platform Fee": s.platform_fee, "Usage Charges": s.usage_charges,
                "GST": s.gst_amount, "Total Amount": s.total_amount,
                "Invoice Status": s.invoice_status or "Live",
            })
        return rows
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")


@router.get("/{tenant_id}/reports/{report_type}")
def get_tenant_report(
    tenant_id: str,
    report_type: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """A tenant-scoped version of SaaS Finance's own platform-wide reports (see
    app/api/v1/saas_finance.py) - the trailing-12-months trend reports read better than one
    single month in isolation would, unlike that page's own current-month-only snapshot."""
    _require_billing_read_access(user, tenant_id)
    if report_type not in TENANT_REPORT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return _tenant_report_rows(db, tenant, report_type, _parse_period_start(month))


@router.get("/{tenant_id}/reports/{report_type}/export")
def export_tenant_report(
    tenant_id: str,
    report_type: str,
    format: str = "csv",
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    _require_billing_read_access(user, tenant_id)
    if report_type not in TENANT_REPORT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    period_start = _parse_period_start(month)
    headers = _TENANT_REPORT_HEADERS[report_type]
    rows = _tenant_report_rows(db, tenant, report_type, period_start)
    filename_base = f"{report_type}-{tenant_id}-{period_start.strftime('%Y-%m')}"

    if format == "xlsx":
        content = rows_to_xlsx(report_type, headers, rows, keys=headers)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{filename_base}.xlsx"
    elif format == "csv":
        content = rows_to_csv(headers, rows, keys=headers)
        media_type = "text/csv"
        filename = f"{filename_base}.csv"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="format must be csv or xlsx")
    return StreamingResponse(
        iter([content]), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{tenant_id}/billing/invoices/{invoice_id}/mark-paid", response_model=InvoiceOut)
def mark_invoice_paid(
    tenant_id: str,
    invoice_id: str,
    payload: MarkInvoicePaid,
    db: Session = Depends(get_db),
    # SaaS Finance owns this in practice day to day - the one write action it gets.
    _=Depends(require_role(SUPER_ADMIN, SAAS_FINANCE)),
) -> InvoiceOut:
    """There is no payment gateway integration yet - a tenant pays by bank transfer to the
    company's own account, outside the platform, and a Super Admin or SaaS Finance user
    marks the invoice paid by
    hand once that payment is actually received. Super Admin only - a Tenant Admin cannot
    mark their own invoice paid."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or invoice.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    invoice.status = "paid" if payload.paid else "unpaid"
    invoice.paid_at = datetime.now(timezone.utc).isoformat() if payload.paid else None
    db.commit()
    db.refresh(invoice)
    return InvoiceOut.model_validate(invoice)


@router.post("/{tenant_id}/irn-pending-key", response_model=TenantOut)
def generate_irn_pending_key(
    tenant_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TenantOut:
    """The Super Admin dashboard's "Generate Link" button, for the standalone, no-login IRN
    Pending page (see app/api/v1/public_irn.py) - a tenant's key is created once, the first
    time this is called for them, and never rotated automatically after that, so a link
    already handed out keeps working. Calling this again for a tenant that already has a key
    is a no-op that just returns it - pressing the button again means "show me the link", not
    "invalidate whatever copy is already in use"."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if not tenant.irn_pending_access_key:
        tenant.irn_pending_access_key = secrets.token_urlsafe(32)
        db.commit()
        db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    """Delete a tenant and everything scoped to it: its admins + operators, templates
    (documents, marks, cross-doc links, reviews), ERP scripts, jobs (docs + field values),
    assignments, access requests, and the sessions of its users. Super Admin only."""
    from sqlalchemy import text

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    params = {"t": tenant_id}
    # Sessions of this tenant's users first (no tenant_id column of their own).
    db.execute(
        text("DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE tenant_id=:t)"),
        params,
    )
    # Everything else is tenant-scoped; children before parents.
    for tbl in (
        "job_field_values",
        "job_documents",
        "jobs",
        "cross_doc_links",
        "field_marks",
        "template_documents",
        "template_reviews",
        "user_template_assignments",
        "erp_scripts",
        "erp_access_requests",
        "custom_fields",
        "job_failure_reports",
        "template_groups",
        "users",
    ):
        db.execute(text(f"DELETE FROM {tbl} WHERE tenant_id=:t"), params)
    db.delete(tenant)
    db.commit()
