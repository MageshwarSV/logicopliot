"""Add template_groups.prealert_shipments - split a multi-shipment air pre-alert, build its AWB.

One pre-alert mail may cover several shipments ("//HAWB1 //HAWB2" in the subject, a row each in
the body). With this on for a template, the email puller makes one job per shipment and builds
an "Air Waybill (Pre-Alert)" PDF from the pre-alert's own data when no waybill is attached.
See TemplateGroup.prealert_shipments / app/core/prealert_shipments.py. Off for every template.

Revision ID: m0preshp01
Revises: l9manent01
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa

revision = "m0preshp01"
down_revision = "l9manent01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_groups",
        sa.Column("prealert_shipments", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("template_groups", "prealert_shipments")
