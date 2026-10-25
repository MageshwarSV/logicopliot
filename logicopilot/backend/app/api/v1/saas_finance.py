"""The SaaS Finance portal's own cross-tenant endpoints - Dashboard, cross-tenant Invoices,
and Reports/export. Everything here is a thin aggregation over app/core/billing.py's existing
per-tenant math (via app/core/finance_aggregation.py) - no pricing/tax logic is reimplemented.

Super Admin is included on every route here the same way it already is on list_tenants
(app/api/v1/tenants.py) - so this can be smoke-tested/used without a second login, not because
Super Admin is expected to live in this portal day to day.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.billing import ensure_invoices_finalized
from app.core.deps import get_db, require_role
from app.core.finance_aggregation import (
    revenue_trend, summarize_all_tenants, total_outstanding_amount,
)
from app.core.finance_export import rows_to_csv, rows_to_xlsx
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.models.user import SAAS_FINANCE, SUPER_ADMIN
from app.schemas.finance import (
    FinanceCustomerRow, FinanceDashboardOut, FinanceInvoiceRow, FinanceRevenuePoint,
)

router = APIRouter(prefix="/finance", tags=["saas-finance"])


def _parse_month(month: str | None) -> date:
    if not month:
        return datetime.now(timezone.utc).date().replace(day=1)
    try:
        return date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="month must be YYYY-MM")


@router.get("/dashboard", response_model=FinanceDashboardOut)
def get_finance_dashboard(
    month: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, SAAS_FINANCE)),
) -> FinanceDashboardOut:
    period_start = _parse_month(month)
    summaries = summarize_all_tenants(db, period_start)
    return FinanceDashboardOut(
        month=period_start.strftime("%Y-%m"),
        total_customers=len(summaries),
        total_jobs=sum(s.total_jobs for s in summaries),
        total_pages=sum(s.total_pages for s in summaries),
        total_usage_charges=round(sum(s.usage_charges for s in summaries), 2),
        total_billable_amount=round(sum(s.total_amount for s in summaries), 2),
        outstanding_amount=round(total_outstanding_amount(db), 2),
        by_customer=[
            FinanceCustomerRow(
                tenant_id=s.tenant_id, tenant_name=s.tenant_name, is_active=s.is_active,
                jobs=s.total_jobs, pages=s.total_pages, platform_fee=s.platform_fee,
                usage_charges=s.usage_charges, gst_amount=s.gst_amount,
                total_amount=s.total_amount, invoice_status=s.invoice_status,
            )
            for s in summaries
        ],
        revenue_trend=[FinanceRevenuePoint(**p) for p in revenue_trend(db, period_start)],
    )


@router.get("/invoices", response_model=list[FinanceInvoiceRow])
def list_finance_invoices(
    tenant_id: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    period_from: str | None = None,
    period_to: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, SAAS_FINANCE)),
) -> list[FinanceInvoiceRow]:
    """Cross-tenant invoice search. Does not itself "generate" anything new to raise - it
    just makes sure every relevant tenant's lazy month-end finalization (see
    ensure_invoices_finalized) has actually run before listing, the same way viewing a single
    tenant's own Billing & Usage page already does, so a tenant nobody has looked at since
    last month ended still shows up here rather than looking like it has no invoices at all."""
    if tenant_id:
        ensure_invoices_finalized(db, tenant_id)
    else:
        for t in db.query(Tenant.id).all():
            ensure_invoices_finalized(db, t.id)

    q = db.query(Invoice).join(Tenant, Tenant.id == Invoice.tenant_id)
    if tenant_id:
        q = q.filter(Invoice.tenant_id == tenant_id)
    if status_filter:
        q = q.filter(Invoice.status == status_filter)
    if period_from:
        q = q.filter(Invoice.period_start >= f"{period_from}-01")
    if period_to:
        q = q.filter(Invoice.period_start <= f"{period_to}-01")
    invoices = q.order_by(Invoice.period_start.desc()).all()
    tenant_names = {t.id: t.name for t in db.query(Tenant).all()}
    return [
        FinanceInvoiceRow(
            id=inv.id, tenant_id=inv.tenant_id, tenant_name=tenant_names.get(inv.tenant_id, "—"),
            invoice_number=inv.invoice_number, period_start=inv.period_start, period_end=inv.period_end,
            total_jobs=inv.total_jobs, total_pages=inv.total_pages, platform_fee=inv.platform_fee,
            usage_charges=inv.usage_charges, gst_amount=inv.gst_amount, total_amount=inv.total_amount,
            status=inv.status, paid_at=inv.paid_at,
        )
        for inv in invoices
    ]


REPORT_TYPES = (
    "monthly-usage", "customer-billing", "revenue-summary",
    "outstanding-payments", "page-usage-charges",
)

_REPORT_HEADERS = {
    "monthly-usage": ["Customer", "Jobs", "Pages", "Shipment Types"],
    "customer-billing": ["Customer", "Jobs", "Pages", "Platform Fee", "Usage Charges", "GST", "Total Amount", "Invoice Status"],
    "revenue-summary": ["Month", "Total Amount"],
    "outstanding-payments": ["Customer", "Invoice Number", "Period", "Total Amount", "Status"],
    "page-usage-charges": ["Customer", "Pages", "Usage Charges"],
}


def _report_rows(db: Session, report_type: str, period_start: date) -> list[dict]:
    if report_type == "revenue-summary":
        return [{"Month": p["month"], "Total Amount": p["total_amount"]}
                for p in revenue_trend(db, period_start)]
    if report_type == "outstanding-payments":
        unpaid = db.query(Invoice).join(Tenant, Tenant.id == Invoice.tenant_id).filter(
            Invoice.status == "unpaid").order_by(Invoice.period_start.desc()).all()
        tenant_names = {t.id: t.name for t in db.query(Tenant).all()}
        return [
            {
                "Customer": tenant_names.get(inv.tenant_id, "—"), "Invoice Number": inv.invoice_number,
                "Period": inv.period_start, "Total Amount": inv.total_amount, "Status": inv.status,
            }
            for inv in unpaid
        ]
    summaries = summarize_all_tenants(db, period_start)
    if report_type == "monthly-usage":
        return [{"Customer": s.tenant_name, "Jobs": s.total_jobs, "Pages": s.total_pages,
                  "Shipment Types": ""} for s in summaries]
    if report_type == "customer-billing":
        return [
            {
                "Customer": s.tenant_name, "Jobs": s.total_jobs, "Pages": s.total_pages,
                "Platform Fee": s.platform_fee, "Usage Charges": s.usage_charges,
                "GST": s.gst_amount, "Total Amount": s.total_amount,
                "Invoice Status": s.invoice_status or "Live",
            }
            for s in summaries
        ]
    if report_type == "page-usage-charges":
        return [{"Customer": s.tenant_name, "Pages": s.total_pages, "Usage Charges": s.usage_charges}
                for s in summaries]
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")


@router.get("/reports/{report_type}")
def get_finance_report(
    report_type: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, SAAS_FINANCE)),
) -> list[dict]:
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")
    return _report_rows(db, report_type, _parse_month(month))


@router.get("/reports/{report_type}/export")
def export_finance_report(
    report_type: str,
    format: str = "csv",
    month: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, SAAS_FINANCE)),
) -> StreamingResponse:
    if report_type not in REPORT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown report type")
    period_start = _parse_month(month)
    headers = _REPORT_HEADERS[report_type]
    rows = _report_rows(db, report_type, period_start)
    filename_base = f"{report_type}-{period_start.strftime('%Y-%m')}"

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

    import io
    return StreamingResponse(
        io.BytesIO(content), media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
