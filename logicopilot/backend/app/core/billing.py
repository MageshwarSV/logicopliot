"""Usage aggregation and bill math for the Billing & Usage feature, plus the lazy
month-end invoice finalization that stands in for a real scheduler (see
ensure_invoices_finalized - there is no cron/APScheduler/Celery anywhere in this codebase,
only email_puller.py's own plain while-True polling thread, so this runs inline on access
instead of on a timer)."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.models.billable_page_event import BillablePageEvent
from app.models.invoice import Invoice
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.tenant_billing_config import TenantBillingConfig

UNASSIGNED_SHIPMENT_TYPE = "Unassigned"


def _month_bounds(period_start: date) -> tuple[datetime, datetime]:
    """[start, end) half-open UTC datetime range covering period_start's calendar month."""
    start_dt = datetime(period_start.year, period_start.month, 1, tzinfo=timezone.utc)
    if period_start.month == 12:
        end_dt = datetime(period_start.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end_dt = datetime(period_start.year, period_start.month + 1, 1, tzinfo=timezone.utc)
    return start_dt, end_dt


class UsageResult(NamedTuple):
    total_jobs: int
    total_pages: int
    by_shipment_type: dict[str, dict[str, int]]


def compute_usage(db: Session, tenant_id: str, period_start: date) -> UsageResult:
    """Every page actually processed counts toward the bill regardless of what happened to its
    job afterward (rejected, duplicate, failed extraction, a later delete, or even the job
    itself being deleted) - the AI-page-processing cost was already incurred the moment the
    page was read. Pages are summed from the permanent BillablePageEvent ledger (written once,
    at the moment each page was processed), NOT from JobDocument.page_count - a delete can zero
    that out or remove the row entirely (see _remove_document_file / DELETE /jobs/{id} in
    app/api/v1/jobs.py), silently erasing cost that was already incurred.

    "Jobs" counts still come straight from Job - a job's own count isn't subject to the same
    delete-erases-history problem an individual page's cost is."""
    start_dt, end_dt = _month_bounds(period_start)

    jobs = (
        db.query(Job.id, Job.group_id)
        .filter(Job.tenant_id == tenant_id, Job.created_at >= start_dt, Job.created_at < end_dt)
        .all()
    )
    job_group = {j.id: j.group_id for j in jobs}
    group_ids = {g for g in job_group.values() if g}
    group_mode: dict[str, str] = {}
    if group_ids:
        group_mode = {
            row.id: (row.mode or UNASSIGNED_SHIPMENT_TYPE)
            for row in db.query(TemplateGroup.id, TemplateGroup.mode)
            .filter(TemplateGroup.id.in_(group_ids)).all()
        }

    by_type: dict[str, dict[str, int]] = {}
    for job_id, group_id in job_group.items():
        shipment_type = group_mode.get(group_id, UNASSIGNED_SHIPMENT_TYPE)
        by_type.setdefault(shipment_type, {"jobs": 0, "pages": 0})["jobs"] += 1

    events = (
        db.query(BillablePageEvent.shipment_type, BillablePageEvent.page_count)
        .filter(
            BillablePageEvent.tenant_id == tenant_id,
            BillablePageEvent.created_at >= start_dt, BillablePageEvent.created_at < end_dt,
        )
        .all()
    )
    total_pages = 0
    for shipment_type, page_count in events:
        by_type.setdefault(shipment_type, {"jobs": 0, "pages": 0})["pages"] += page_count or 0
        total_pages += page_count or 0

    return UsageResult(total_jobs=len(job_group), total_pages=total_pages, by_shipment_type=by_type)


def record_billable_pages(db: Session, job: Job, page_count: int) -> None:
    """Write one permanent ledger row the moment pages are actually processed - call this at
    every _render_pages() call site, right after page_count is computed (email-pulled job
    creation, a manual upload/re-upload on Document Capture, Smart Upload, and assigning a
    staged Unclassified page to a slot), in the same transaction as the JobDocument write so
    both commit or roll back together. A page_count of 0 writes nothing - there is nothing
    billable about an empty/failed read. Snapshots job_reference and shipment_type now, since
    the job (or its template group) can be changed or deleted later."""
    if not page_count:
        return
    shipment_type = UNASSIGNED_SHIPMENT_TYPE
    if job.group_id:
        group = db.get(TemplateGroup, job.group_id)
        if group is not None and group.mode:
            shipment_type = group.mode
    db.add(BillablePageEvent(
        tenant_id=job.tenant_id, job_id=job.id, job_reference=job.reference,
        shipment_type=shipment_type, page_count=page_count,
    ))


class BillBreakdown(NamedTuple):
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


def _platform_fee_proration(config: TenantBillingConfig, period_start: date) -> tuple[int, int]:
    """(billed_days, month_days) for period_start's calendar month. Without a
    billing_start_date set, every month bills in full (billed_days == month_days) - the
    original, pre-proration behavior, unchanged for every tenant that never sets one. With one
    set: the calendar month it falls in bills only for the days from that date onward (go-live
    on the 15th of a 31-day month bills 17 of 31 days); every month strictly before it bills
    nothing (not yet live); every month after it bills in full."""
    month_days = calendar.monthrange(period_start.year, period_start.month)[1]
    if not config.billing_start_date:
        return month_days, month_days
    try:
        start_date = date.fromisoformat(config.billing_start_date)
    except ValueError:
        return month_days, month_days
    period_month = (period_start.year, period_start.month)
    start_month = (start_date.year, start_date.month)
    if period_month < start_month:
        return 0, month_days
    if period_month == start_month:
        return max(month_days - start_date.day + 1, 0), month_days
    return month_days, month_days


def default_billing_config(tenant_id: str) -> TenantBillingConfig:
    """A transient (never added to the session) config carrying the standard published rates,
    for a tenant with no TenantBillingConfig row saved yet. Assigned explicitly rather than
    relying on the model's own `default=` kwargs, which only apply at actual INSERT/flush
    time - a plain unflushed instance would otherwise read back None on every column and
    blow up calculate_bill's arithmetic. Keep in sync with TenantBillingConfig's own defaults."""
    config = TenantBillingConfig(tenant_id=tenant_id)
    config.cargora_monthly_fee = 4999.0
    config.cargora_tier1_rate_per_page = 2.50
    config.cargora_tier1_page_limit = 25000
    config.cargora_tier2_rate_per_page = 2.00
    config.gst_percent = 18.0
    config.billing_start_date = None
    return config


def calculate_bill(config: TenantBillingConfig, total_pages: int, period_start: date) -> BillBreakdown:
    billed_days, month_days = _platform_fee_proration(config, period_start)
    platform_fee = round(config.cargora_monthly_fee * billed_days / month_days, 2)
    tier1_pages = min(total_pages, config.cargora_tier1_page_limit)
    tier2_pages = max(0, total_pages - config.cargora_tier1_page_limit)
    tier1_amount = round(tier1_pages * config.cargora_tier1_rate_per_page, 2)
    tier2_amount = round(tier2_pages * config.cargora_tier2_rate_per_page, 2)
    usage_charges = round(tier1_amount + tier2_amount, 2)
    subtotal = round(platform_fee + usage_charges, 2)
    gst_amount = round(subtotal * config.gst_percent / 100, 2)
    total_amount = round(subtotal + gst_amount, 2)
    return BillBreakdown(
        platform_fee=platform_fee, platform_fee_billed_days=billed_days, platform_fee_month_days=month_days,
        tier1_rate=config.cargora_tier1_rate_per_page, tier1_pages=tier1_pages, tier1_amount=tier1_amount,
        tier2_rate=config.cargora_tier2_rate_per_page, tier2_pages=tier2_pages, tier2_amount=tier2_amount,
        usage_charges=usage_charges, subtotal=subtotal,
        gst_percent=config.gst_percent, gst_amount=gst_amount, total_amount=total_amount,
    )


def _invoice_number(tenant_id: str, period_start: date) -> str:
    return f"INV-{period_start:%Y%m}-{tenant_id[:8].upper()}"


def ensure_invoices_finalized(db: Session, tenant_id: str) -> None:
    """Raises an invoice for every calendar month that has fully ended since this tenant's
    billing was first configured, if one doesn't already exist for it. Called on every access
    to this tenant's billing data (list_invoices, get_billing_usage) - cheap once caught up,
    since it's a no-op the moment every past month already has its row. The current,
    still-running month is never finalized here - callers always compute that one live.

    Starts counting from TenantBillingConfig.created_at's month, or from billing_start_date's
    month when that is set and later (a tenant onboarded, then actually gone live partway
    through a later month) - a tenant with no billing config saved yet has nothing to raise,
    and one just configured today doesn't retroactively get invoices manufactured for years of
    history it was never billed for."""
    config = db.get(TenantBillingConfig, tenant_id)
    if config is None:
        return

    today = datetime.now(timezone.utc).date()
    current_month_start = today.replace(day=1)
    start_basis = config.created_at.date()
    if config.billing_start_date:
        try:
            parsed = date.fromisoformat(config.billing_start_date)
            start_basis = max(start_basis, parsed)
        except ValueError:
            pass
    month = start_basis.replace(day=1)

    while month < current_month_start:
        exists = (
            db.query(Invoice.id)
            .filter(Invoice.tenant_id == tenant_id, Invoice.period_start == month.isoformat())
            .first()
        )
        if exists is None:
            usage = compute_usage(db, tenant_id, month)
            bill = calculate_bill(config, usage.total_pages, month)
            last_day = calendar.monthrange(month.year, month.month)[1]
            db.add(Invoice(
                tenant_id=tenant_id,
                invoice_number=_invoice_number(tenant_id, month),
                period_start=month.isoformat(),
                period_end=month.replace(day=last_day).isoformat(),
                platform_fee=bill.platform_fee,
                platform_fee_billed_days=bill.platform_fee_billed_days,
                platform_fee_month_days=bill.platform_fee_month_days,
                total_jobs=usage.total_jobs, total_pages=usage.total_pages,
                tier1_rate=bill.tier1_rate, tier1_pages=bill.tier1_pages, tier1_amount=bill.tier1_amount,
                tier2_rate=bill.tier2_rate, tier2_pages=bill.tier2_pages, tier2_amount=bill.tier2_amount,
                usage_charges=bill.usage_charges, subtotal=bill.subtotal,
                gst_percent=bill.gst_percent, gst_amount=bill.gst_amount, total_amount=bill.total_amount,
                by_shipment_type=usage.by_shipment_type,
                status="unpaid",
            ))
        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)
    db.commit()
