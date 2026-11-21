from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# created - a Razorpay order exists, the Tenant Admin has (or had) the payment window open
# paid    - Razorpay confirmed the money; the invoice is marked paid
# failed  - Razorpay reported the attempt failed (the tenant can simply press Pay now again)
PAYMENT_STATUSES = ("created", "paid", "failed")


class InvoicePayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One "Pay now" attempt on one invoice through Razorpay. Every press makes a new row with
    its own Razorpay order, so an abandoned window never blocks a later attempt. The amounts are
    fixed when the order is created - the invoice total plus the gateway charge passed on to the
    tenant - and the payment is only accepted for exactly that amount."""

    __tablename__ = "invoice_payments"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    razorpay_order_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    invoice_amount: Mapped[float] = mapped_column(Float, nullable=False)
    fee_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee_gst_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="created")
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paid_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
