import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.billing import calculate_bill, compute_usage, default_billing_config, ensure_invoices_finalized
from app.core.deps import get_current_user, get_db, require_role
from app.core.modes import MODES
from app.core.security import hash_password
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.models.tenant_billing_config import TenantBillingConfig
from app.models.user import ADMIN, GK2, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.tenant import (
    TRACKCONTAINER_PLANS,
    BillingUsageOut,
    InvoiceOut,
    MarkInvoicePaid,
    ShipmentTypeUsageOut,
    TenantBillingConfigOut,
    TenantBillingConfigUpdate,
    TenantCreate,
    TenantOut,
    TenantUpdate,
)

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
    _=Depends(require_role(SUPER_ADMIN, ADMIN)),
) -> list[TenantOut]:
    tenants = db.query(Tenant).all()
    return [TenantOut.model_validate(t) for t in tenants]


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantOut:
    """Super admins can look up any tenant; everyone else only their own (404 otherwise) —
    this is how a Tenant Admin/Operator dashboard shows its own tenant's name."""
    if user.role != SUPER_ADMIN and user.tenant_id != tenant_id:
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
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantBillingConfigOut:
    """Pure reference data for the Super Admin's own records - what this tenant is billed for
    CARGORA itself and the TrackContainer.in integration. Returns the model's own defaults
    (the standard published rates) when nothing has been saved for this tenant yet, rather
    than 404ing - there is nothing wrong with a tenant nobody has configured this for."""
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


def _require_billing_read_access(user: User, tenant_id: str) -> None:
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN):
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
            usage_charges=invoice.usage_charges, subtotal=invoice.subtotal,
            gst_percent=invoice.gst_percent, gst_amount=invoice.gst_amount, total_amount=invoice.total_amount,
        )

    config = db.get(TenantBillingConfig, tenant_id) or default_billing_config(tenant_id)
    usage = compute_usage(db, tenant_id, period_start)
    bill = calculate_bill(config, usage.total_pages, period_start)
    return BillingUsageOut(
        month=period_start.strftime("%Y-%m"), is_finalized=False,
        total_jobs=usage.total_jobs, total_pages=usage.total_pages,
        by_shipment_type=[
            ShipmentTypeUsageOut(shipment_type=k, jobs=v["jobs"], pages=v["pages"])
            for k, v in sorted(usage.by_shipment_type.items())
        ],
        platform_fee=bill.platform_fee,
        platform_fee_billed_days=bill.platform_fee_billed_days,
        platform_fee_month_days=bill.platform_fee_month_days,
        tier1_rate=bill.tier1_rate, tier1_pages=bill.tier1_pages,
        tier1_amount=bill.tier1_amount, tier2_rate=bill.tier2_rate, tier2_pages=bill.tier2_pages,
        tier2_amount=bill.tier2_amount, usage_charges=bill.usage_charges, subtotal=bill.subtotal,
        gst_percent=bill.gst_percent, gst_amount=bill.gst_amount, total_amount=bill.total_amount,
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


@router.post("/{tenant_id}/billing/invoices/{invoice_id}/mark-paid", response_model=InvoiceOut)
def mark_invoice_paid(
    tenant_id: str,
    invoice_id: str,
    payload: MarkInvoicePaid,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> InvoiceOut:
    """There is no payment gateway integration yet - a tenant pays by bank transfer to the
    company's own account, outside the platform, and a Super Admin marks the invoice paid by
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
