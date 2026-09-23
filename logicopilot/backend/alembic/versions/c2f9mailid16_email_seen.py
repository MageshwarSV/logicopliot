r"""email_seen: one row per message the puller has already examined

Reading a message is expensive - every attachment is OCR'd and the text put to a model to work
out which customer it belongs to. Without a record of what has been examined, a message that
matches nobody pays that cost on every single poll, forever.

Marking the mail \Seen would stop the repeat too, but it hides the message from whoever owns
the mailbox and destroys the chance to pull it again once the customer is configured. So the
mail is left untouched and the fact we looked is recorded instead.

Revision ID: c2f9mailid16
Revises: b1e7excel15
"""
import sqlalchemy as sa
from alembic import op

revision = "c2f9mailid16"
down_revision = "b1e7excel15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_seen",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["template_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_email_seen_message_id", "email_seen", ["message_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_email_seen_message_id", table_name="email_seen")
    op.drop_table("email_seen")
