"""Add api_cost_daily, api_cost_daily_customer, api_cost_runs - daily OpenAI cost and page usage
(Super Admin -> API Cost & Usage), collected every night and reconciled the next day.

Revision ID: d3apicost01
Revises: d2promptchg01
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "d3apicost01"
down_revision = "d2promptchg01"
branch_labels = None
depends_on = None


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "api_cost_daily",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usd_inr_rate", sa.Float(), nullable=True),
        sa.Column("rate_source", sa.String(length=100), nullable=True),
        sa.Column("cost_inr", sa.Float(), nullable=True),
        sa.Column("pages_billed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("original_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rerun_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classified_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unclassified_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_per_page_usd", sa.Float(), nullable=True),
        sa.Column("cost_per_page_inr", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="provisional"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        *_ts(),
        sa.UniqueConstraint("usage_date", "project_id", name="uq_api_cost_daily_day_project"),
    )
    op.create_index("ix_api_cost_daily_usage_date", "api_cost_daily", ["usage_date"])
    op.create_table(
        "api_cost_daily_customer",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_name", sa.String(length=255), nullable=True),
        sa.Column("pages_billed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("original_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rerun_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classified_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unclassified_pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_jobs", sa.Integer(), nullable=False, server_default="0"),
        *_ts(),
        sa.UniqueConstraint("usage_date", "tenant_id", name="uq_api_cost_daily_customer"),
    )
    op.create_index("ix_api_cost_daily_customer_usage_date", "api_cost_daily_customer", ["usage_date"])
    op.create_table(
        "api_cost_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("usage_dates", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        *_ts(),
    )


def downgrade() -> None:
    op.drop_table("api_cost_runs")
    op.drop_index("ix_api_cost_daily_customer_usage_date", table_name="api_cost_daily_customer")
    op.drop_table("api_cost_daily_customer")
    op.drop_index("ix_api_cost_daily_usage_date", table_name="api_cost_daily")
    op.drop_table("api_cost_daily")
