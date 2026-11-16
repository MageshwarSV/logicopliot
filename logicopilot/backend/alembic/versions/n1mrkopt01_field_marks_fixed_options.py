"""Add field_marks.fixed_options / dropdown_style - a closed list of choices on a DOCUMENT field.

Until now only a custom field could carry its own list (CustomField.fixed_options); a field
marked on a document was a free text box whatever it held. A quantity unit read off an invoice
is one of a known set of codes, and the operator should pick it, not type it. Mirrors the two
CustomField columns exactly, so the job screen draws both the same way. Null on every existing
mark - nothing changes until an admin gives a field a list.

Revision ID: n1mrkopt01
Revises: m0preshp01
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa

revision = "n1mrkopt01"
down_revision = "m0preshp01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("field_marks", sa.Column("fixed_options", sa.JSON(), nullable=True))
    op.add_column("field_marks", sa.Column("dropdown_style", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("field_marks", "dropdown_style")
    op.drop_column("field_marks", "fixed_options")
