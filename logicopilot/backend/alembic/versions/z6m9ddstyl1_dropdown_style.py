"""Add custom_fields.dropdown_style - fixed_options only: how the admin's fixed choices
render on the Job Run screen. None/"plain" (every field before this existed) is a plain
closed <select>, unchanged; "search" is a searchable combobox that still only accepts one of
the listed values; "search_free" is the same searchable combobox, but typing a value that
isn't in the list still saves it. Set once in the Wizard's own Custom Tag modal - a long
fixed_options list (a country list, say) reads better searchable than scrolled through a
plain <select>, without needing a frontend code change to pick between them.

Additive and nullable - every existing field with fixed_options keeps rendering as the plain
<select> it renders as today.

Revision ID: z6m9ddstyl1
Revises: y5k8displ1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "z6m9ddstyl1"
down_revision = "y5k8displ1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("dropdown_style", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "dropdown_style")
