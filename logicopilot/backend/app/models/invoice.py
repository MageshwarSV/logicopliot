from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# A tenant pays, or doesn't, outside the platform for now (bank transfer to our own account) -
# there is no payment gateway integration yet. "unpaid" is every invoice's starting state;
# a Super Admin marks it "paid" by hand once payment is actually received.
INVOICE_STATUSES = ("unpaid", "paid")


class Invoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One tenant's CARGORA bill for one calendar month, frozen the first time anyone asks for
    usage after that month has actually ended (see ensure_invoices_finalized in
    app/api/v1/billing.py) - never for the CURRENT, still-running month, which the Billing &
    Usage page always computes live instead and shows as an unfinalized "this month so far"
    figure. Every number on an invoice is a SNAPSHOT taken at that moment: a job deleted or a
    tenant's rates changed afterward must never change what an already-raised invoice says,
    the same way a real paper invoice doesn't retroactively change once it's been sent."""

    __tablename__ = "invoices"
    __table_args__ = (
        # One invoice per tenant per calendar month - ensure_invoices_finalized checks for an
        # existing row before creating one, but this is the real guarantee against a race
        # (two requests finalizing the same month at once) actually producing two invoices.
        UniqueConstraint("tenant_id", "period_start", name="uq_invoices_tenant_period"),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Human-facing number, e.g. "INV-2026-10-001" - unique on its own, not just a display of
    # period_start, so a tenant can be given a stable reference even if numbering conventions
    # change later.
    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    # The first day of the billed month (e.g. 2026-10-01) - the real identity of "which month"
    # this invoice covers; period_end is always the last day of that same month, kept as its
    # own column only so a query never has to compute it.
    period_start: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-01
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-<last day>

    # Everything below is a SNAPSHOT of that month's usage and that tenant's rates AT THE
    # MOMENT this invoice was raised - never recomputed afterward.
    platform_fee: Mapped[float] = mapped_column(Float, nullable=False)
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    tier1_rate: Mapped[float] = mapped_column(Float, nullable=False)
    tier1_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    tier1_amount: Mapped[float] = mapped_column(Float, nullable=False)
    tier2_rate: Mapped[float] = mapped_column(Float, nullable=False)
    tier2_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    tier2_amount: Mapped[float] = mapped_column(Float, nullable=False)
    usage_charges: Mapped[float] = mapped_column(Float, nullable=False)  # tier1_amount + tier2_amount
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)  # platform_fee + usage_charges
    gst_percent: Mapped[float] = mapped_column(Float, nullable=False)
    gst_amount: Mapped[float] = mapped_column(Float, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)  # subtotal + gst_amount
    # Usage by shipment type, e.g. {"Sea Import": {"jobs": 500, "pages": 10000}, ...} - the
    # mockup's own "Usage Summary by Shipment Type" table, frozen the same way everything
    # else here is.
    by_shipment_type: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default="unpaid")
    paid_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
