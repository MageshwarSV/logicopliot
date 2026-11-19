"""Axiora's own details printed on every Cargora tax invoice - one row, edited on the SaaS
Finance "Invoice settings" page. Each invoice keeps a snapshot of these at the moment it is
raised (Invoice.seller), so a later change never rewrites an invoice already sent."""
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin

SINGLETON_ID = "default"


class InvoiceSettings(Base, TimestampMixin):
    __tablename__ = "invoice_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=SINGLETON_ID)
    seller_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True, default="India")
    gstin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sac_platform: Mapped[str | None] = mapped_column(String(10), nullable=True, default="998315")
    sac_usage: Mapped[str | None] = mapped_column(String(10), nullable=True, default="998313")
    terms: Mapped[str | None] = mapped_column(String(60), nullable=True, default="Due on Receipt")
    due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, default="Thanks for your business.")
    bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    number_prefix: Mapped[str | None] = mapped_column(String(10), nullable=True, default="INV")
