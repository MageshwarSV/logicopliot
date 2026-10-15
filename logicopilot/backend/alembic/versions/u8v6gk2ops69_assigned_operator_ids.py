"""assigned_operator_ids on users: which GK1/operator users' jobs a gk2 user is allowed to
see/act on, by id. Independent of assigned_modes (that scopes by template mode, this scopes by
who submitted the job). Null/empty means unrestricted (every existing gk2 account keeps seeing
every operator's jobs, unchanged) - see the model's own comment for why this field's null
semantics are the opposite of assigned_modes'.

Revision ID: u8v6gk2ops69
Revises: t7u5reason68
"""
from alembic import op
import sqlalchemy as sa

revision = "u8v6gk2ops69"
down_revision = "t7u5reason68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("assigned_operator_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "assigned_operator_ids")
