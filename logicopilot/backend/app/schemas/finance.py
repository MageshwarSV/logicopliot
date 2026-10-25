"""Response shapes for the SaaS Finance portal's own cross-tenant endpoints
(app/api/v1/saas_finance.py). Every figure here is produced by
app/core/finance_aggregation.py, which itself only calls app/core/billing.py's existing
per-tenant math - nothing here computes a price or a tax amount on its own."""
from pydantic import BaseModel, ConfigDict


class FinanceCustomerRow(BaseModel):
    tenant_id: str
    tenant_name: str
    is_active: bool
    jobs: int
    pages: int
    platform_fee: float
    usage_charges: float
    gst_amount: float
    total_amount: float
    # "unpaid" | "paid" | None - None means this month is still live, not yet an invoice.
    invoice_status: str | None


class FinanceRevenuePoint(BaseModel):
    month: str  # YYYY-MM
    total_amount: float


class FinanceDashboardOut(BaseModel):
    month: str  # YYYY-MM
    total_customers: int
    total_jobs: int
    total_pages: int
    total_usage_charges: float
    total_billable_amount: float
    # A running balance across every tenant's unpaid invoices - independent of `month` above,
    # see finance_aggregation.total_outstanding_amount's own docstring.
    outstanding_amount: float
    by_customer: list[FinanceCustomerRow]
    revenue_trend: list[FinanceRevenuePoint]


class FinanceInvoiceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    tenant_name: str
    invoice_number: str
    period_start: str
    period_end: str
    total_jobs: int
    total_pages: int
    platform_fee: float
    usage_charges: float
    gst_amount: float
    total_amount: float
    status: str
    paid_at: str | None
