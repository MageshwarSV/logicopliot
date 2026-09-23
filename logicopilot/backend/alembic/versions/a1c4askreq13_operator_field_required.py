"""ask_operator fields can be mandatory or optional

Until now, ticking "ask the operator" always blocked Submit Entry until the operator filled
the field in. Some fields genuinely are not known at entry time — the IGM number and date
arrive from the shipping line later — so the operator must be able to send the job through
without them.

`ask_operator_required` splits the two cases:
  True  (default) — mandatory: the operator must supply it, Submit Entry stays blocked
  False           — optional: still shown and still asked for, but never blocks Submit

Defaults to True on both tables, so every field that exists today keeps behaving exactly as
it does now.

Revision ID: a1c4askreq13
Revises: a1c3ckpt12
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c4askreq13"
down_revision = "a1c3ckpt12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column(
                "ask_operator_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "ask_operator_required")
