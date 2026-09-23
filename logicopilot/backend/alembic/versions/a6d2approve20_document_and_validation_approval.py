"""per-document and validation approval, pending (unidentified) emails

Data Extraction and Data Validation both need an explicit "Approved and Proceed" from the
operator before the job may move on — an empty list of cross-checks was being read as
"nothing to do" rather than "nobody has looked yet".

Also adds pending_emails: mail the auto-router could not place on its own (no customer
matched, or more than one did) is now held with its attachments for a person to assign a
template to by hand, instead of being read once and thrown away.

Revision ID: a6d2approve20
Revises: a5c1multi19
"""
import sqlalchemy as sa
from alembic import op

revision = "a6d2approve20"
down_revision = "a5c1multi19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_documents",
                  sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs",
                  sa.Column("validation_approved", sa.Boolean(), nullable=False,
                            server_default=sa.false()))

    op.create_table(
        "pending_emails",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("resolved_group_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_job_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["resolved_group_id"], ["template_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index("ix_pending_emails_message_id", "pending_emails", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_emails_message_id", table_name="pending_emails")
    op.drop_table("pending_emails")
    op.drop_column("jobs", "validation_approved")
    op.drop_column("job_documents", "approved")
