"""store raw OCR + keyed-out data in the database

job_documents.extracted_json        - raw OCR for that uploaded document
jobs.extracted_keyouted_data        - assembled {document: {label: value}} for the job

Both are additive and nullable, so existing jobs are unaffected: they simply carry NULL
until they are re-extracted.

Revision ID: a1b7extracted06
Revises: a1b6jobfail05
Create Date: 2026-08-05
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b7extracted06"
down_revision: Union[str, Sequence[str], None] = "a1b6jobfail05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_documents", sa.Column("extracted_json", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("extracted_keyouted_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "extracted_keyouted_data")
    op.drop_column("job_documents", "extracted_json")
