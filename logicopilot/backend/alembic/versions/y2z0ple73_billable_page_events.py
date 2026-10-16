"""Add billable_page_events: a durable, append-only ledger of pages actually processed,
used for billing instead of JobDocument.page_count (which a delete or job removal can erase).

Revision ID: y2z0ple73
Revises: x1y9bsd72
Create Date: 2026-10-16
"""

import uuid

from alembic import op
import sqlalchemy as sa

revision = "y2z0ple73"
down_revision = "x1y9bsd72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billable_page_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("job_reference", sa.String(length=255), nullable=False),
        sa.Column("shipment_type", sa.String(length=50), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Backfill: every document currently on record becomes one seed ledger event, dated at
    # its OWN upload time (not "now") - otherwise every page processed before this ledger
    # existed would vanish from the bill the moment this table came into use, which is exactly
    # the undercounting bug this table exists to fix. Can only seed from what still exists
    # today - a document or job already deleted before this migration ran has no row left
    # anywhere to seed from, so that history is unavoidably gone.
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT jd.tenant_id AS tenant_id, jd.job_id AS job_id, jd.page_count AS page_count,
               jd.created_at AS created_at, j.reference AS job_reference, tg.mode AS shipment_type
        FROM job_documents jd
        JOIN jobs j ON j.id = jd.job_id
        LEFT JOIN template_groups tg ON tg.id = j.group_id
        WHERE jd.file_path IS NOT NULL AND jd.page_count > 0
    """)).fetchall()

    insert = sa.text("""
        INSERT INTO billable_page_events
            (id, tenant_id, job_id, job_reference, shipment_type, page_count, created_at, updated_at)
        VALUES (:id, :tenant_id, :job_id, :job_reference, :shipment_type, :page_count, :created_at, :created_at)
    """)
    for row in rows:
        bind.execute(insert, {
            "id": str(uuid.uuid4()),
            "tenant_id": row.tenant_id,
            "job_id": row.job_id,
            "job_reference": row.job_reference or "",
            "shipment_type": row.shipment_type or "Unassigned",
            "page_count": row.page_count,
            "created_at": row.created_at,
        })


def downgrade() -> None:
    op.drop_table("billable_page_events")
