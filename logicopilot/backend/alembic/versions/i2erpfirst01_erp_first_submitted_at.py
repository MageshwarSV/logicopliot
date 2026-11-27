"""jobs.erp_first_submitted_at - the first time a job was sent to the ERP (retries keep it).

Existing jobs get their last send (erp_submitted_at) - the only time stored before this; for a
job retried before today that is later than its real first send.

Revision ID: i2erpfirst01
Revises: i1aiaided01
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "i2erpfirst01"
down_revision = "i1aiaided01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_first_submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE jobs SET erp_first_submitted_at = erp_submitted_at WHERE erp_submitted_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("jobs", "erp_first_submitted_at")
