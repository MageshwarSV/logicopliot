"""archive_passwords - zip passwords a customer uses, tried automatically on locked zips.

Encrypted at rest (app/core/mail_crypto), never returned by the API. Empty to start with: each
environment's Tenant Admin enters them on the "Zip passwords" screen.

Revision ID: u3zippwd01
Revises: u2excelsheet01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u3zippwd01"
down_revision = "u2excelsheet01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archive_passwords",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sender_domains", sa.JSON(), nullable=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(36), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_archive_passwords_tenant_id", "archive_passwords", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_archive_passwords_tenant_id", table_name="archive_passwords")
    op.drop_table("archive_passwords")
