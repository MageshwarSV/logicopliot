"""skip_material_master on custom_fields: a kind="lookup" field whose own key is not a
product at all (a container type, say) must never try the customer's material master, because
its DESCRIPTION-match fallback can exact-match whatever unrelated product happens to share the
same line's row number and hand back THAT product's CTH as if it were this field's own answer.
Opt-in per field, same shape as fuzzy_match above it, since the vast majority of lookup fields
(Dump CTH Number and the like) ARE genuinely keyed on a product and must keep trying the master
exactly as before.

Revision ID: k3m1skipmaster64
Revises: m2k0keypool63
"""
from alembic import op
import sqlalchemy as sa

revision = "k3m1skipmaster64"
down_revision = "m2k0keypool63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("skip_material_master", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "skip_material_master")
