"""Auto ERP Submission: a job goes to the ERP the moment extraction finishes, no review in
Cargora - allowed per tenant by the SaaS Admin, switched on per template (and branch) by the
Tenant Admin. Everything defaults OFF, so no existing tenant or template changes.

Revision ID: f1autoerp01
Revises: e2erpfieldcfg01
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa

revision = "f1autoerp01"
down_revision = "e2erpfieldcfg01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("auto_erp_allowed", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    op.add_column("tenants", sa.Column("auto_erp_rules", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("auto_erp_submitted", sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    op.add_column("jobs", sa.Column("auto_erp_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "auto_erp_note")
    op.drop_column("jobs", "auto_erp_submitted")
    op.drop_column("tenants", "auto_erp_rules")
    op.drop_column("tenants", "auto_erp_allowed")
