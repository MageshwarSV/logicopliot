"""Add custom_fields.standalone_group_heading - mirrors the column FieldMark already has
(added in an earlier migration), so a CustomField can also carry its own section heading on
the Job Run page instead of relying only on JobRunPage.tsx's hardcoded FIELD_GROUPS_BY_DOC_TYPE
map. Several real fields (Destination, CustomsHouseCode, Shipment Port Code, ERP Consignee
Country/Name, ERP Importer Address / Branch Code) are CustomFields, not FieldMarks - without
this column they would fall out of their named section into a generic "Other" bucket the
moment any FieldMark on the same document gets a heading set.

Additive and nullable - every existing custom field keeps behaving exactly as it does today
(no heading of its own, falls back to the hardcoded map) with no backfill needed by this
migration itself.

Revision ID: x4j7grphd1
Revises: w3d8inscur1
Create Date: 2026-11-05
"""

from alembic import op
import sqlalchemy as sa

revision = "x4j7grphd1"
down_revision = "w3d8inscur1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("standalone_group_heading", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "standalone_group_heading")
