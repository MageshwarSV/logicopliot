"""jobs.erp_captured — values read back OUT of the ERP after entry

Until now a submission returned nothing identifying. The ERP prints the reference it
generated (a Bill of Entry number, an ICEGATE acknowledgement) on the confirmation screen and
we discarded it, so a completed job could not be reconciled against the ERP afterwards. The new
`get_text` / `assert_text` steps write what they read here.

Shape: {"<name>": "<value read from the page>", ...} e.g. {"be_number": "1234567"}

Additive and nullable — existing jobs and every existing step keep working untouched.

Revision ID: a1c1capture10
Revises: a1c0multival09
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c1capture10"
down_revision = "a1c0multival09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_captured", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "erp_captured")
