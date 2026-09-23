"""jobs.eta_date — a GK1-set target date, drives the ETA boxes on the operator dashboard.

Revision ID: t7q1etadate39
Revises: s6p0foundpos38
"""
from alembic import op
import sqlalchemy as sa

revision = "t7q1etadate39"
down_revision = "s6p0foundpos38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("eta_date", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "eta_date")
