"""Large-job lane (step 1): template_groups.large_job_lane, system_settings
large_job_parallel_calls / practice_ai_mode.

large_job_lane: a mail carrying a zip larger than the template's large_zip_mb is handed to its
own worker (one large job at a time) instead of the email cycle's shared pool, so a big
consolidation neither blocks the mail cycle nor takes the workers small jobs use. Off for every
existing template - switched on per template on Super Admin -> Document Processing.

large_job_parallel_calls: parallel AI/OCR calls inside one large job (used from step 2).
practice_ai_mode: off | record | replay - free testing; only honoured on a server whose .env
has PRACTICE_AI_ALLOWED=1 (SIT), so it can never take effect in production.

Revision ID: u7largejob01
Revises: u6taxinvoice01
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "u7largejob01"
down_revision = "u6taxinvoice01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("large_job_lane", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("system_settings", sa.Column("large_job_parallel_calls", sa.Integer(), nullable=False, server_default="6"))
    op.add_column("system_settings", sa.Column("practice_ai_mode", sa.String(10), nullable=False, server_default="off"))


def downgrade() -> None:
    op.drop_column("system_settings", "practice_ai_mode")
    op.drop_column("system_settings", "large_job_parallel_calls")
    op.drop_column("template_groups", "large_job_lane")
