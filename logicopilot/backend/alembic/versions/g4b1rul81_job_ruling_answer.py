"""Add jobs.ruling_answer - the operator's own answer to the template's ruling question (e.g.
"CIF"), persisted so a later re-evaluation (in particular the one Submit-for-Approval itself now
runs) uses what was already answered instead of asking the same question again. Part of removing
the separate "Hold" job status: a job the ruling cannot resolve now simply stays wherever it
already was, and Submit/Final Approve/Submit Entry refuse outright until this is set (or the AI
can read the value straight off the documents). Additive and nullable - every existing job simply
has no answer yet, same as a job whose ruling already resolves itself from the documents.

Revision ID: g4b1rul81
Revises: f3a9sep80
Create Date: 2026-10-23
"""

from alembic import op
import sqlalchemy as sa

revision = "g4b1rul81"
down_revision = "f3a9sep80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("ruling_answer", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "ruling_answer")
