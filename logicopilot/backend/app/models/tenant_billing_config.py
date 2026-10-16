from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class TenantBillingConfig(Base, TimestampMixin):
    """Per-tenant pricing/billing reference numbers a Super Admin records against third-party
    services this tenant is billed for - CARGORA's own platform fee and AI-page processing
    tiers, and the TrackContainer.in plan this tenant is on. Pure reference data: nothing
    elsewhere in the app reads these to enforce or calculate an actual bill. One row per
    tenant, created on first save rather than at tenant creation - a tenant with no row yet
    simply has nothing configured, not an error."""

    __tablename__ = "tenant_billing_configs"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )

    # CARGORA — platform fee + two-tier per-page AI processing rate.
    cargora_monthly_fee: Mapped[float] = mapped_column(Float, nullable=False, default=4999.0)
    cargora_tier1_rate_per_page: Mapped[float] = mapped_column(Float, nullable=False, default=2.50)
    cargora_tier1_page_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=25000)
    cargora_tier2_rate_per_page: Mapped[float] = mapped_column(Float, nullable=False, default=2.00)

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
