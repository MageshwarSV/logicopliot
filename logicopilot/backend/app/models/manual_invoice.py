from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# draft -> issued (number given, locked) -> paid / unpaid again ; issued -> cancelled (number kept)
MANUAL_INVOICE_STATUSES = ("draft", "issued", "paid", "cancelled")


class ManualInvoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A one-off invoice written by hand (SaaS Finance > Invoices > New Invoice, 2026-11-27) -
    e.g. "Separate ERP server - November 2026". Not tied to a month or to usage, so it lives
    apart from Invoice (one usage invoice per customer per month), but takes its number from the
    SAME series: INV/<financial year>/<next number> across both tables
    (app/core/manual_invoices.next_number).

    A draft has no number and can be edited or deleted. Issuing gives it the next number, takes
    a snapshot of seller / buyer details and the CGST+SGST / IGST split, and locks it. A wrong
    issued invoice is cancelled, never deleted - its number stays used."""

    __tablename__ = "manual_invoices"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    invoice_number: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    financial_year: Mapped[str | None] = mapped_column(String(7), nullable=True, index=True)
    fy_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_date: Mapped[str] = mapped_column(String(10), nullable=False)
    due_date: Mapped[str] = mapped_column(String(10), nullable=False)
    period_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # [{"description", "sac", "qty", "rate", "amount"}]
    lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    gst_percent: Mapped[float] = mapped_column(Float, nullable=False, default=18.0)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gst_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    cgst_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    sgst_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    igst_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    seller: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    buyer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    issued_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    paid_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cancelled_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
