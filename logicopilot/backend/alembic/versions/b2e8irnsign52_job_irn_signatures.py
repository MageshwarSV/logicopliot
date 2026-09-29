"""job_irn_signatures — one row per document header (a job template document, or a
Supporting Document) on a job GK1 sent down the "Approval for IRN" path: its DSC-signed file
plus its IRN number, uploaded one document at a time through the public IRN Pending link.
Once every header on a job has a row here, the job is moved on automatically (see
_kick_off_real_erp_submission in app/api/v1/jobs.py, called from public_irn.py's sign
endpoint).

Revision ID: b2e8irnsign52
Revises: f9c5irnapprove51
"""
from alembic import op
import sqlalchemy as sa

revision = "b2e8irnsign52"
down_revision = "f9c5irnapprove51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_irn_signatures",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(length=36),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_ref", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("irn_number", sa.String(length=255), nullable=False),
        sa.Column("stored_as", sa.String(length=255), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "doc_ref", name="uq_job_irn_signature_doc_ref"),
    )
    op.create_index("ix_job_irn_signatures_tenant_id", "job_irn_signatures", ["tenant_id"])
    op.create_index("ix_job_irn_signatures_job_id", "job_irn_signatures", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_irn_signatures_job_id", table_name="job_irn_signatures")
    op.drop_index("ix_job_irn_signatures_tenant_id", table_name="job_irn_signatures")
    op.drop_table("job_irn_signatures")
