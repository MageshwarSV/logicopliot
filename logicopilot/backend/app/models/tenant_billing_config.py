from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class TenantBillingConfig(Base, TimestampMixin):
    """Per-tenant pricing/billing numbers a Super Admin sets - CARGORA's own platform fee and
    AI-page processing tiers, and the TrackContainer.in plan this tenant is on. These ARE what
    every real invoice is computed from (see app/core/billing.py's calculate_bill) - not pure
    reference data, despite this docstring once saying so. One row per tenant, created on
    first save rather than at tenant creation - a tenant with no row yet simply has nothing
    configured, not an error (default_billing_config supplies the standard published rates in
    that case)."""

    __tablename__ = "tenant_billing_configs"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )

    # CARGORA — platform fee + two-tier per-page AI processing rate.
    cargora_monthly_fee: Mapped[float] = mapped_column(Float, nullable=False, default=4999.0)
    cargora_tier1_rate_per_page: Mapped[float] = mapped_column(Float, nullable=False, default=2.50)
    cargora_tier1_page_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=25000)
    cargora_tier2_rate_per_page: Mapped[float] = mapped_column(Float, nullable=False, default=2.00)
    # A page that never got matched to any document (shown to the operator as "Not Required" -
    # see JobBillingRow.final_unclassified_pages) is billed at this flat rate INSTEAD of the
    # Tier 1/2 rate above, not in addition to it - replaces, not stacks. Same ₹1.00 default for
    # every tenant; editable per tenant like every other rate here. See calculate_bill's own
    # not_required_pages parameter for exactly how this is applied.
    not_required_page_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.00)

    # TrackContainer.in — which plan this tenant is on (null = none/not subscribed), plus
    # both plans' own price/limit so a changed standard rate doesn't need a new column.
    trackcontainer_plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trackcontainer_starter_price: Mapped[float] = mapped_column(Float, nullable=False, default=2999.0)
    trackcontainer_starter_container_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    trackcontainer_professional_price: Mapped[float] = mapped_column(Float, nullable=False, default=5999.0)
    trackcontainer_professional_container_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1500
    )

    # Applied to every invoice this tenant is billed (platform fee + usage charges) - a plain
    # percentage (18.0 means 18%), editable per tenant in case treatment ever differs.
    gst_percent: Mapped[float] = mapped_column(Float, nullable=False, default=18.0)

    # Optional go-live date (YYYY-MM-DD). Null = no proration, billing is treated as always
    # having been live (every tenant configured before this field existed keeps behaving
    # exactly as before). Set once when onboarding completes partway through a month: the
    # platform fee for THAT one calendar month is pro-rated to the days actually live (day 15
    # of a 31-day month bills 17/31 of the fee) - see app/core/billing.py's
    # _platform_fee_proration. Every month before it is never billed; every month after bills
    # in full.
    billing_start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
