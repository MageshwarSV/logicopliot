"""Daily OpenAI cost and page usage (Super Admin -> API Cost & Usage). Written by
app.core.api_cost - see there for how a day is collected, reconciled and retried."""
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

TOTAL_PROJECT = "__account_total__"


class ApiCostDaily(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per usage day and OpenAI project, plus one ACCOUNT TOTAL row (project_id =
    TOTAL_PROJECT) holding the day's whole cost and the platform's page figures. A second run
    for the same day updates these rows - (usage_date, project_id) is unique."""

    __tablename__ = "api_cost_daily"
    __table_args__ = (UniqueConstraint("usage_date", "project_id", name="uq_api_cost_daily_day_project"),)

    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usd_inr_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Page figures - on the ACCOUNT TOTAL row only (cost stays per project, never per customer).
    pages_billed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rerun_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classified_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unclassified_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_per_page_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_page_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    # provisional - fetched before OpenAI finished the day; final - reconciled; failed.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="provisional")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ApiCostDailyCustomer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pages each customer used that day. No cost here, by design."""

    __tablename__ = "api_cost_daily_customer"
    __table_args__ = (UniqueConstraint("usage_date", "tenant_id", name="uq_api_cost_daily_customer"),)

    usage_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pages_billed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rerun_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classified_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unclassified_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ApiCostRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution: the 11:55 PM collection, the next-day reconciliation, or a manual run."""

    __tablename__ = "api_cost_runs"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)          # daily | reconcile | manual
    usage_dates: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running | ok | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
