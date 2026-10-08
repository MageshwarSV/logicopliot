"""system_settings.extraction_engine — "ocr_gpt4o_mini" (default) or "gpt5_mini_vision"

Which engine production document classification and field extraction use, live-editable
from Settings (Super Admin), with zero behavior change until a Super Admin flips it. Never
read by the Template Wizard's own demo-extract/prompt-generation flow, which always stays on
OCR text + gpt-4o-mini. See app/models/system_setting.py.

Revision ID: l0j6engine62
Revises: k1f9unmatched61
"""
from alembic import op
import sqlalchemy as sa

revision = "l0j6engine62"
down_revision = "k1f9unmatched61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("extraction_engine", sa.Text(), nullable=False,
                 server_default="ocr_gpt4o_mini"),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "extraction_engine")
