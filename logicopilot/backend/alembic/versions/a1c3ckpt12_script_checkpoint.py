"""erp_scripts: checkpoint_index + stay_open

A script may mark one recorded step as its CHECKPOINT — the point reached after logging in and
navigating to the entry screen. Everything up to and including that step is setup; everything
after it is the actual entry.

With `stay_open` on, the browser session reached at the checkpoint is reused by the next job
instead of logging in again from scratch. Both are opt-in: a script with no checkpoint behaves
exactly as before — open, run every step, close.

Nullable and defaulted, so every existing script is unaffected.

Revision ID: a1c3ckpt12
Revises: a1c2runrec11
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c3ckpt12"
down_revision = "a1c2runrec11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0-based index into steps[]. NULL = no checkpoint = current behaviour.
    op.add_column("erp_scripts", sa.Column("checkpoint_index", sa.Integer(), nullable=True))
    op.add_column(
        "erp_scripts",
        sa.Column("stay_open", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("erp_scripts", "stay_open")
    op.drop_column("erp_scripts", "checkpoint_index")
