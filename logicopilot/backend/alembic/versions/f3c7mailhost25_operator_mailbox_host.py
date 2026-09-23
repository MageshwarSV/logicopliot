"""operator mailbox host — users.mail_host, the exact IMAP host verified against

Zoho runs separate regional data centers (imap.zoho.in, imap.zoho.eu, ...); a provider
name alone ("zoho") is not enough to reconnect correctly. Nullable, so an existing
connected mailbox (saved before this existed) is unaffected - the poller falls back to
the provider's global default host until this is re-verified.

Revision ID: f3c7mailhost25
Revises: e2b6mail24
"""
from alembic import op
import sqlalchemy as sa

revision = "f3c7mailhost25"
down_revision = "e2b6mail24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mail_host", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mail_host")
