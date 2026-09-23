r"""several files in one document slot, and values that know which file they came from

A job can carry three invoices and three packing lists. Until now a slot held exactly one
file - uploading a second replaced the first - and a value recorded only which SLOT it came
from, so three invoice numbers were three rows all labelled "invoice_no" with nothing to
tell them apart.

Two columns fix that:

  job_documents.file_index    position within the slot, 0 is the row created with the job
  job_documents.set_index     which invoice-set this file was paired into

  job_field_values.job_document_id   the exact file this value was read from
  job_field_values.set_index         that file's set, denormalised so the workbook can
                                     group a product line under the invoice it was billed on

set_index is filled in AFTER extraction, by matching invoice numbers across the slots. It is
deliberately NOT the upload order: an operator can upload invoice 1 and packing list 3 in any
sequence, and pairing them by position would silently verify the wrong two documents against
each other - which is worse than not verifying at all, because it reports a green tick.

Existing rows are backfilled to file_index 0 / set_index 1, which is exactly what a
one-file-per-slot job already meant.

Revision ID: a5c1multi19
Revises: e4b8hist18
"""
import sqlalchemy as sa
from alembic import op

revision = "a5c1multi19"
down_revision = "e4b8hist18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_documents",
                  sa.Column("file_index", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("job_documents",
                  sa.Column("set_index", sa.Integer(), nullable=True))
    op.add_column("job_documents",
                  sa.Column("original_name", sa.String(length=255), nullable=True))

    op.add_column("job_field_values",
                  sa.Column("job_document_id", sa.String(length=36), nullable=True))
    op.add_column("job_field_values",
                  sa.Column("set_index", sa.Integer(), nullable=True))
    op.create_index("ix_job_field_values_job_document_id",
                    "job_field_values", ["job_document_id"])

    # Everything that already exists was a single-file slot, which is set 1.
    op.execute("UPDATE job_documents SET set_index = 1 WHERE set_index IS NULL")
    op.execute("UPDATE job_field_values SET set_index = 1 WHERE set_index IS NULL")


def downgrade() -> None:
    op.drop_index("ix_job_field_values_job_document_id", table_name="job_field_values")
    op.drop_column("job_field_values", "set_index")
    op.drop_column("job_field_values", "job_document_id")
    op.drop_column("job_documents", "original_name")
    op.drop_column("job_documents", "set_index")
    op.drop_column("job_documents", "file_index")
