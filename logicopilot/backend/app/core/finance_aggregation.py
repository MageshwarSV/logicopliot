"""Platform-wide (cross-tenant) billing figures for the SaaS Finance portal - every function
here is a thin loop over tenants calling app/core/billing.py's existing per-tenant functions
(compute_usage/calculate_bill/ensure_invoices_finalized) unmodified. No pricing math is
reimplemented here; this module only adds tenants together.

Kept separate from billing.py itself (rather than extending get_billing_usage there) so the
existing, already-working tenant-facing Billing & Usage page can never be put at risk by this
feature - it is not read by, or touched by, anything in this module.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.billing import calculate_bill, compute_usage, default_billing_config, ensure_invoices_finalized
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.models.tenant_billing_config import TenantBillingConfig


@dataclass
class TenantBillSummary:
    tenant_id: str
    tenant_name: str
    is_active: bool
    is_finalized: bool
    invoice_id: str | None
    invoice_status: str | None  # "unpaid" | "paid" | None (current, still-live month)
    total_jobs: int
    total_pages: int
    platform_fee: float
    usage_charges: float
    gst_amount: float
    subtotal: float
    total_amount: float


def summarize_tenant_bill(db: Session, tenant: Tenant, period_start: date) -> TenantBillSummary:
    """One tenant's figures for one calendar month - current month computed live (never an
    Invoice row yet, same rule the tenant-facing page itself follows), a past month read from
    its (auto-finalized-on-first-view) Invoice row. Mirrors get_billing_usage's own current-vs-
    past branch in app/api/v1/tenants.py exactly, without touching that function."""
    today = datetime.now(timezone.utc).date()
    ensure_invoices_finalized(db, tenant.id)

    if period_start < today.replace(day=1):
        invoice = (
            db.query(Invoice)
            .filter(Invoice.tenant_id == tenant.id, Invoice.period_start == period_start.isoformat())
            .first()
        )
        if invoice is None:
            # No billing config existed yet for this tenant in this month - nothing to show,
            # not an error (a tenant onboarded after this month has genuinely zero usage here).
            return TenantBillSummary(
                tenant_id=tenant.id, tenant_name=tenant.name, is_active=tenant.is_active,
                is_finalized=False, invoice_id=None, invoice_status=None,
                total_jobs=0, total_pages=0, platform_fee=0.0, usage_charges=0.0,
                gst_amount=0.0, subtotal=0.0, total_amount=0.0,
            )
        return TenantBillSummary(
            tenant_id=tenant.id, tenant_name=tenant.name, is_active=tenant.is_active,
            is_finalized=True, invoice_id=invoice.id, invoice_status=invoice.status,
            total_jobs=invoice.total_jobs, total_pages=invoice.total_pages,
            platform_fee=invoice.platform_fee, usage_charges=invoice.usage_charges,
            gst_amount=invoice.gst_amount, subtotal=invoice.subtotal, total_amount=invoice.total_amount,
        )

    config = db.get(TenantBillingConfig, tenant.id) or default_billing_config(tenant.id)
    usage = compute_usage(db, tenant.id, period_start)
    bill = calculate_bill(config, usage.total_pages, period_start)
    return TenantBillSummary(
        tenant_id=tenant.id, tenant_name=tenant.name, is_active=tenant.is_active,
        is_finalized=False, invoice_id=None, invoice_status=None,
        total_jobs=usage.total_jobs, total_pages=usage.total_pages,
        platform_fee=bill.platform_fee, usage_charges=bill.usage_charges,
        gst_amount=bill.gst_amount, subtotal=bill.subtotal, total_amount=bill.total_amount,
    )


def summarize_all_tenants(db: Session, period_start: date) -> list[TenantBillSummary]:
    """Every tenant's figures for one month, in a stable (name) order. The only place in this
    whole feature that loops `Tenant` directly - every other aggregation reuses this."""
    tenants = db.query(Tenant).order_by(Tenant.name).all()
    return [summarize_tenant_bill(db, t, period_start) for t in tenants]


def total_outstanding_amount(db: Session) -> float:
    """Every tenant's unpaid-invoice total, platform-wide - a running balance, independent of
    whichever month's dashboard is currently selected (an invoice from three months ago that
    is still unpaid is still owed today)."""
    total = db.query(func.sum(Invoice.total_amount)).filter(Invoice.status == "unpaid").scalar()
    return float(total or 0.0)


def trailing_months(period_start: date, count: int) -> list[date]:
    """`count` calendar months ending at (and including) period_start, oldest first."""
    months: list[date] = []
    y, m = period_start.year, period_start.month
    for _ in range(count):
        months.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(months))


def revenue_trend(db: Session, period_start: date, months: int = 12) -> list[dict]:
    """[{month: 'YYYY-MM', total_amount: float}, ...] summed across every tenant, for the
    trailing `months` calendar months ending at period_start."""
    out = []
    for month_start in trailing_months(period_start, months):
        summaries = summarize_all_tenants(db, month_start)
        out.append({
            "month": month_start.strftime("%Y-%m"),
            "total_amount": round(sum(s.total_amount for s in summaries), 2),
        })
    return out
