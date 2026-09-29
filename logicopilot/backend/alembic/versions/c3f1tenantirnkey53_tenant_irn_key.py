"""tenants.irn_pending_access_key - one key per tenant that gates their own IRN Pending link
(app/api/v1/public_irn.py), generated from the Super Admin dashboard's "Generate Link" button
(POST /tenants/{id}/irn-pending-key). Replaces the single hardcoded key every tenant's jobs
used to share - each tenant now sees only its own jobs through its own link.

Data step: 4S Logistics already has a live link out in the world (the original hardcoded
key), so its row is seeded with that exact value here rather than starting blank - the link
already handed out keeps working unchanged. Every other tenant starts with no key until a
super admin presses "Generate Link" for them.

Revision ID: c3f1tenantirnkey53
Revises: b2e8irnsign52
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f1tenantirnkey53"
down_revision = "b2e8irnsign52"
branch_labels = None
depends_on = None

# The same literal app/api/v1/public_irn.py's PUBLIC_ACCESS_KEY constant carries - kept in
# sync manually (not imported: a migration must not depend on application code drifting out
# from under it).
_ORIGINAL_KEY = "PvWxx-1WQ8ZlCcmZpNFZGNxMYPsPZKKBPWdpVZ9mygY"


def upgrade() -> None:
    op.add_column("tenants", sa.Column("irn_pending_access_key", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_tenants_irn_pending_access_key", "tenants", ["irn_pending_access_key"]
    )
    op.execute(
        sa.text("UPDATE tenants SET irn_pending_access_key = :key WHERE name = '4S Logistics'")
        .bindparams(key=_ORIGINAL_KEY)
    )


def downgrade() -> None:
    op.drop_constraint("uq_tenants_irn_pending_access_key", "tenants", type_="unique")
    op.drop_column("tenants", "irn_pending_access_key")
