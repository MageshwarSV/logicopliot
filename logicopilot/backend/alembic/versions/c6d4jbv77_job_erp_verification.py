"""Add jobs.erp_verified_at / jobs.erp_verified_by_id - a human confirming the AI/ERP
automation actually submitted a job, closing the loop gk2_status == "submitted" otherwise
never moves on from. Defaults to unverified for every existing row.

Revision ID: c6d4jbv77
Revises: b5c3pwd76
Create Date: 2026-10-18
"""

from alembic import op
import sqlalchemy as sa

revision = "c6d4jbv77"
down_revision = "b5c3pwd76"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("erp_verified_by_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_jobs_erp_verified_by_id", "jobs", "users",
        ["erp_verified_by_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_erp_verified_by_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "erp_verified_by_id")
    op.drop_column("jobs", "erp_verified_at")
