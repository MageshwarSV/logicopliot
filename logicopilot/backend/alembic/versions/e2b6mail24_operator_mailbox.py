"""operator mailbox — mail_provider, mail_email, mail_app_password_encrypted on users

An operator can now connect their own Gmail/Zoho inbox (validated by an IMAP login test
before saving), polled the same way the tenant's shared inbox already is. All three columns
are nullable and unrelated to any existing one, so no existing row is affected.

Revision ID: e2b6mail24
Revises: d1a5admin23
"""
from alembic import op
import sqlalchemy as sa

revision = "e2b6mail24"
down_revision = "d1a5admin23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mail_provider", sa.String(length=20), nullable=True))
    op.add_column("users", sa.Column("mail_email", sa.String(length=320), nullable=True))
    op.add_column("users", sa.Column("mail_app_password_encrypted", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mail_app_password_encrypted")
    op.drop_column("users", "mail_email")
    op.drop_column("users", "mail_provider")
