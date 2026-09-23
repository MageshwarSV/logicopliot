r"""custom_fields: a value that comes from the customer's material master

The CTH (HS code) of a part is settled long before any shipment and appears on no document, so
it was left for an operator to type on every line of every job. The customer already has the
answer in their own export - material code -> commodity code - so the field can simply look it
up, which is how the same customer is handled in customflow.

Revision ID: d3a7lookup17
Revises: c2f9mailid16
"""
import sqlalchemy as sa
from alembic import op

revision = "d3a7lookup17"
down_revision = "c2f9mailid16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields",
                  sa.Column("lookup_key_label", sa.String(length=100), nullable=True))
    op.add_column("custom_fields",
                  sa.Column("lookup_match_columns", sa.JSON(), nullable=True))
    op.add_column("custom_fields",
                  sa.Column("lookup_return_column", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "lookup_return_column")
    op.drop_column("custom_fields", "lookup_match_columns")
    op.drop_column("custom_fields", "lookup_key_label")
