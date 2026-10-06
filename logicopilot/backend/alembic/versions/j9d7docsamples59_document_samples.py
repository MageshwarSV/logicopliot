"""document_samples - what a customer's own documents of each type look like.

Every time an operator puts a file into a named slot by hand they state what that
document is, more reliably than any classifier can infer it. Those labels were
discarded. This table keeps the top of each such document against the slot it was
put in, so the classifier can be shown them next time - the prompt already has a
place for exactly this ("the customer's own sample of this document") which
nothing had ever filled.

Purely additive: a new table, no existing column touched, nothing backfilled. On a
server that has not run this yet, reading the samples fails softly and
classification behaves exactly as it does today.

Revision ID: j9d7docsamples59
Revises: j0e8standalonehdg60
"""
from alembic import op
import sqlalchemy as sa

revision = "j9d7docsamples59"
down_revision = "j0e8standalonehdg60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_samples",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("template_document_id", sa.String(length=36), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        # Which upload taught us this, so a bad sample can be traced back. Deliberately
        # not a foreign key: the job document may be deleted later, and losing the
        # sample with it would unlearn something the operator correctly taught us.
        sa.Column("job_document_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_document_id"], ["template_documents.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_samples_tenant_id", "document_samples", ["tenant_id"])
    # The lookup every classification does: the samples for this job's slots.
    op.create_index("ix_document_samples_template_document_id", "document_samples",
                    ["template_document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_samples_template_document_id",
                  table_name="document_samples")
    op.drop_index("ix_document_samples_tenant_id", table_name="document_samples")
    op.drop_table("document_samples")
