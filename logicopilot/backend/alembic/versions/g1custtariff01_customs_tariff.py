"""Indian Customs Tariff (ICEGATE): the global 8-digit CTH master, for every tenant and stream.
Empty until the SaaS Admin uploads the file; nothing reads it yet, so no job changes.

Revision ID: g1custtariff01
Revises: f1autoerp01
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "g1custtariff01"
down_revision = "f1autoerp01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customs_tariff_uploads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=True),
        sa.Column("total_codes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "customs_tariff",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cth", sa.String(8), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rate", sa.String(100), nullable=True),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("preferential_rate", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("upload_id", sa.String(36),
                  sa.ForeignKey("customs_tariff_uploads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customs_tariff_cth", "customs_tariff", ["cth"], unique=True)
    op.create_index("ix_customs_tariff_is_active", "customs_tariff", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_customs_tariff_is_active", table_name="customs_tariff")
    op.drop_index("ix_customs_tariff_cth", table_name="customs_tariff")
    op.drop_table("customs_tariff")
    op.drop_table("customs_tariff_uploads")
