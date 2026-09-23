"""Persist the ERP run result on the job

Until now erp_status / erp_log / erp_reason / erp_screenshot / erp_failed_field /
erp_failed_value were set on the RESPONSE object only — there were no such columns. Reload the
page after an entry and every trace of what the engine did was gone.

That blocks the feature this migration exists for: showing the operator WHICH field the ERP
rejected, in red, so they can correct it and re-run. The failed field has to survive a page
reload for that to work at all.

All nullable, all additive — existing jobs are untouched.

Revision ID: a1c2runrec11
Revises: a1c1capture10
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c2runrec11"
down_revision = "a1c1capture10"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("erp_status", sa.String(20)),        # ok | failed | error | duplicated | no_script
    ("erp_reason", sa.Text()),            # why it stopped, in the engine's words
    ("erp_diagnosis", sa.Text()),         # what AI saw on the final screen, in plain words
    ("erp_final_url", sa.String(1000)),
    ("erp_failed_field", sa.String(255)),  # data field label the ERP rejected -> shown in RED
    ("erp_failed_value", sa.Text()),       # the value it would not accept
    ("erp_log", sa.JSON()),                # ordered step-by-step log of the run
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("jobs", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("jobs", name)
