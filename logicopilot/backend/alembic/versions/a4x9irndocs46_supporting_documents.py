"""supporting_documents — arbitrary files an operator attaches to a job under their own label
(GK1's IRN Documents Upload / GK2's IRN Processing), purely storage, no extraction. Also
jobs.irn_documents_done — persists GK1's IRN stage tick, previously client-only React state.

Revision ID: a4x9irndocs46
Revises: z3w7reftable45
"""
from alembic import op
import sqlalchemy as sa

revision = "a4x9irndocs46"
down_revision = "z3w7reftable45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("irn_documents_done", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "supporting_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(length=36),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_supporting_documents_tenant_id", "supporting_documents", ["tenant_id"])
    op.create_index("ix_supporting_documents_job_id", "supporting_documents", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_supporting_documents_job_id", table_name="supporting_documents")
    op.drop_index("ix_supporting_documents_tenant_id", table_name="supporting_documents")
    op.drop_table("supporting_documents")
    op.drop_column("jobs", "irn_documents_done")
