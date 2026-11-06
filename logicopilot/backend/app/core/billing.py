"""Usage aggregation and bill math for the Billing & Usage feature, plus the lazy
month-end invoice finalization that stands in for a real scheduler (see
ensure_invoices_finalized - there is no cron/APScheduler/Celery anywhere in this codebase,
only email_puller.py's own plain while-True polling thread, so this runs inline on access
instead of on a timer)."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from sqlalchemy import func
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
    # Of total_pages: billed as a job's own first-time processing vs an explicit
    # reclassify_documents re-run - see BillablePageEvent.is_rerun's own docstring.
    # pages_original + pages_rerun always equals total_pages.
    pages_original: int = 0
    pages_rerun: int = 0
    # Of every job with at least one billing event THIS PERIOD (by event date, same scoping as
    # total_pages - not total_jobs above, which counts by the job's own creation date instead),
    # how many currently still have real content in a document slot vs still sitting in the
    # Unclassified pile. Read live, not from the ledger (the ledger only ever recorded how many
    # pages were processed, never where they ended up) - so this can drift from pages_original
    # if a page was deleted afterward without a matching rerun. final_classified_pages +
    # final_unclassified_pages is normally close to pages_original, not guaranteed identical.
    final_classified_pages: int = 0
    final_unclassified_pages: int = 0
    # Of that same set of billed-this-period jobs, how many still exist vs have been hard-
    # deleted since (DELETE /jobs/{id} removes the row entirely - see BillablePageEvent's own
    # docstring for why the ledger has to survive that). Deliberately a DIFFERENT count from
    # total_jobs above (which is scoped by job creation date, not billing-event date) - this
    # answers "of what we billed this period, what's still here," not "what got created here."
    active_jobs_billed: int = 0
    deleted_jobs_billed: int = 0


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
        db.query(
            BillablePageEvent.job_id, BillablePageEvent.shipment_type,
            BillablePageEvent.page_count, BillablePageEvent.is_rerun,
        )
        .filter(
            BillablePageEvent.tenant_id == tenant_id,
            BillablePageEvent.created_at >= start_dt, BillablePageEvent.created_at < end_dt,
        )
        .all()
    )
    total_pages = 0
    pages_original = 0
    pages_rerun = 0
    billed_job_ids: set[str] = set()
    for event_job_id, shipment_type, page_count, is_rerun in events:
        page_count = page_count or 0
        by_type.setdefault(shipment_type, {"jobs": 0, "pages": 0})["pages"] += page_count
        total_pages += page_count
        if is_rerun:
            pages_rerun += page_count
        else:
            pages_original += page_count
        billed_job_ids.add(event_job_id)

    # Of the jobs this period actually billed for (by event date, not creation date - see
    # UsageResult's own docstring), how many still exist, and - for those - how many of their
    # pages are currently sitting in a real document slot vs still unresolved in Unclassified.
    # Read live, deliberately: the ledger only ever recorded that pages WERE processed, never
    # where they ended up.
    from app.models.job import JobDocument, UnmatchedUploadPage

    still_exist = {
        row.id for row in db.query(Job.id).filter(Job.id.in_(billed_job_ids)).all()
    } if billed_job_ids else set()
    final_classified_pages = 0
    final_unclassified_pages = 0
    if still_exist:
        final_classified_pages = (
            db.query(func.coalesce(func.sum(JobDocument.page_count), 0))
            .filter(JobDocument.job_id.in_(still_exist), JobDocument.file_path.isnot(None))
            .scalar() or 0
        )
        final_unclassified_pages = (
            db.query(func.count(UnmatchedUploadPage.id))
            .filter(UnmatchedUploadPage.job_id.in_(still_exist))
            .scalar() or 0
        )

    return UsageResult(
        total_jobs=len(job_group), total_pages=total_pages, by_shipment_type=by_type,
        pages_original=pages_original, pages_rerun=pages_rerun,
        final_classified_pages=final_classified_pages,
        final_unclassified_pages=final_unclassified_pages,
        active_jobs_billed=len(still_exist),
        deleted_jobs_billed=len(billed_job_ids) - len(still_exist),
    )


class JobBillingRow(NamedTuple):
    job_id: str
    job_reference: str
    shipment_type: str
    status: str  # a live Job.status value, or "deleted" if the job no longer exists
    started_at: datetime
    original_pages: int
    rerun_pages: int
    # Read live, same as compute_usage's own final_classified_pages/final_unclassified_pages -
    # always 0/0 for a deleted job, since there is no live state left to read. See that
    # function's own docstring for why these are not guaranteed to equal original_pages.
    final_classified_pages: int
    final_unclassified_pages: int
    # original_pages minus whatever of it is still accounted for live (classified +
    # unclassified) - a page that was billed once (real OCR cost incurred) and later removed
    # (a duplicate/wrong upload deleted, with no rerun to replace it) stays billed forever, by
    # design - see record_billable_pages' own docstring - but is no longer counted as
    # classified or unclassified anywhere. Never negative: a rerun's own extra pages land in
    # rerun_pages, not here, so they can't make this go below zero.
    deleted_pages: int


def _job_billing_query(
    db: Session, tenant_id: str, period_start: date, *,
    search: str | None = None, shipment_type: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
):
    """Shared query behind list_job_billing/list_job_billing_export - finds which jobs belong to
    this period, by each job's own REAL start time: the live Job.created_at when the job still
    exists, else the ledger's own earliest billing-event time for a since-deleted job (there is
    no other record of a deleted job's start). This is the exact value shown in the table's own
    "Job Start Time" column and what it sorts by - picking a month, or narrowing with the date
    range picker, filters by that same real start time, so what you see is what you asked for:
    a job whose Job Start Time doesn't fall in the window you picked is deliberately excluded,
    not included because it merely had unrelated billing activity (e.g. a rerun, or this
    session's one-time backfill) land inside that window.

    Does NOT compute original_pages/rerun_pages - see _resolve_job_billing_rows, which sums
    those ALL-TIME per job (not scoped to this period) because "Original Pages" means every
    page this job has ever had OCR'd for it (e.g. "how many pages came in by email"), independent
    of which period its Job Start Time falls into."""
    start_dt, end_dt = _month_bounds(period_start)

    ledger = (
        db.query(
            BillablePageEvent.job_id.label("job_id"),
            func.max(BillablePageEvent.job_reference).label("job_reference"),
            func.max(BillablePageEvent.shipment_type).label("shipment_type"),
            func.min(BillablePageEvent.created_at).label("ledger_started_at"),
        )
        .filter(BillablePageEvent.tenant_id == tenant_id)
        .group_by(BillablePageEvent.job_id)
        .subquery()
    )
    real_started_at = func.coalesce(Job.created_at, ledger.c.ledger_started_at)

    q = (
        db.query(
            ledger.c.job_id, ledger.c.job_reference, ledger.c.shipment_type,
            real_started_at.label("started_at"),
        )
        .outerjoin(Job, Job.id == ledger.c.job_id)
        .filter(real_started_at >= start_dt, real_started_at < end_dt)
    )
    if search:
        q = q.filter(ledger.c.job_reference.ilike(f"%{search}%"))
    if shipment_type:
        q = q.filter(ledger.c.shipment_type == shipment_type)
    if date_from:
        q = q.filter(real_started_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to:
        exclusive_end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc) + timedelta(days=1)
        q = q.filter(real_started_at < exclusive_end)
    return q


def _resolve_job_billing_rows(db: Session, agg_rows: list) -> list[JobBillingRow]:
    """Turn query rows into JobBillingRow: status comes from the live Job row when one still
    exists, else "deleted"; started_at is already each row's real start time (see
    _job_billing_query's own docstring); classified/unclassified counts are read live, same as
    compute_usage's own fields; original_pages/rerun_pages are each job's ALL-TIME ledger
    totals (see _job_billing_query's own docstring for why this is deliberately not scoped to
    the period that selected these jobs)."""
    job_ids = [r.job_id for r in agg_rows]
    jobs_by_id = (
        {j.id: j for j in db.query(Job.id, Job.status).filter(Job.id.in_(job_ids)).all()}
        if job_ids else {}
    )

    pages_by_job: dict[str, tuple[int, int]] = {}
    if job_ids:
        for jid, orig, rerun in (
            db.query(
                BillablePageEvent.job_id,
                func.sum(
                    func.coalesce(BillablePageEvent.page_count, 0)
                ).filter(BillablePageEvent.is_rerun.is_(False)),
                func.sum(
                    func.coalesce(BillablePageEvent.page_count, 0)
                ).filter(BillablePageEvent.is_rerun.is_(True)),
            )
            .filter(BillablePageEvent.job_id.in_(job_ids))
            .group_by(BillablePageEvent.job_id)
            .all()
        ):
            pages_by_job[jid] = (orig or 0, rerun or 0)

    from app.models.job import JobDocument, UnmatchedUploadPage

    classified_by_job: dict[str, int] = {}
    unclassified_by_job: dict[str, int] = {}
    live_ids = [jid for jid in job_ids if jid in jobs_by_id]
    if live_ids:
        for jid, total_pages_row in (
            db.query(JobDocument.job_id, func.coalesce(func.sum(JobDocument.page_count), 0))
            .filter(JobDocument.job_id.in_(live_ids), JobDocument.file_path.isnot(None))
            .group_by(JobDocument.job_id)
            .all()
        ):
            classified_by_job[jid] = total_pages_row
        for jid, cnt in (
            db.query(UnmatchedUploadPage.job_id, func.count(UnmatchedUploadPage.id))
            .filter(UnmatchedUploadPage.job_id.in_(live_ids))
            .group_by(UnmatchedUploadPage.job_id)
            .all()
        ):
            unclassified_by_job[jid] = cnt

    rows = []
    for r in agg_rows:
        live = jobs_by_id.get(r.job_id)
        orig, rerun = pages_by_job.get(r.job_id, (0, 0))
        classified = classified_by_job.get(r.job_id, 0)
        unclassified = unclassified_by_job.get(r.job_id, 0)
        rows.append(JobBillingRow(
            job_id=r.job_id, job_reference=r.job_reference, shipment_type=r.shipment_type,
            status=live.status if live else "deleted",
            started_at=r.started_at,
            original_pages=orig, rerun_pages=rerun,
            final_classified_pages=classified,
            final_unclassified_pages=unclassified,
            deleted_pages=max(0, orig - classified - unclassified),
        ))
    return rows


def list_job_billing(
    db: Session, tenant_id: str, period_start: date, *,
    search: str | None = None, shipment_type: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    page: int = 1, page_size: int = 10,
) -> tuple[list[JobBillingRow], int]:
    """Per-job breakdown for the Billing & Usage page's own job table - one row per job whose
    REAL start time (live Job.created_at, or the ledger's own earliest event for a since-deleted
    job) falls in this period - a DIFFERENT scoping from compute_usage's own active_jobs_billed/
    deleted_jobs_billed (which count by billing-EVENT date instead), see _job_billing_query's
    own docstring for why. job_reference/shipment_type come from the ledger's own snapshot
    (survives a later hard delete - see BillablePageEvent's own docstring).

    Returns (rows, total_matching_jobs) - total is BEFORE pagination, for the caller's own page
    count. Rows are returned newest-started-first."""
    all_rows = _job_billing_query(
        db, tenant_id, period_start,
        search=search, shipment_type=shipment_type, date_from=date_from, date_to=date_to,
    ).all()
    total = len(all_rows)
    all_rows.sort(key=lambda r: r.started_at, reverse=True)
    page_rows = all_rows[(page - 1) * page_size: (page - 1) * page_size + page_size]
    return _resolve_job_billing_rows(db, page_rows), total


def list_job_billing_export(
    db: Session, tenant_id: str, period_start: date, *,
    search: str | None = None, shipment_type: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
) -> list[JobBillingRow]:
    """Same filters as list_job_billing, with no pagination - every matching row, for the
    "Export" button's CSV download."""
    all_rows = _job_billing_query(
        db, tenant_id, period_start,
        search=search, shipment_type=shipment_type, date_from=date_from, date_to=date_to,
    ).all()
    all_rows.sort(key=lambda r: r.started_at, reverse=True)
    return _resolve_job_billing_rows(db, all_rows)


def record_billable_pages(
    db: Session, job: Job, page_count: int, *, is_rerun: bool = False,
) -> None:
    """Write one permanent ledger row the moment pages are actually processed - call this at
    every page-OCR call site (email-pulled job creation, a manual upload/re-upload on Document
    Capture, Smart Upload's own classification pass, and reclassify_documents), in the same
    transaction as the JobDocument/staging write so both commit or roll back together. A
    page_count of 0 writes nothing - there is nothing billable about an empty/failed read.
    Snapshots job_reference and shipment_type now, since the job (or its template group) can be
    changed or deleted later.

    is_rerun: True only for reclassify_documents - an explicit admin re-run of an already-
    processed job's classification, genuinely re-reading pages already billed once before.
    False (the default) covers everything else, including a large file's own multi-pass OCR
    past the per-file cap - that is still ONE original pass, not a rerun, even though it writes
    more than one ledger row for the same job. See BillablePageEvent.is_rerun's own docstring."""
    if not page_count:
        return
    shipment_type = UNASSIGNED_SHIPMENT_TYPE
    if job.group_id:
        group = db.get(TemplateGroup, job.group_id)
        if group is not None and group.mode:
            shipment_type = group.mode
    db.add(BillablePageEvent(
        tenant_id=job.tenant_id, job_id=job.id, job_reference=job.reference,
        shipment_type=shipment_type, page_count=page_count, is_rerun=is_rerun,
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
