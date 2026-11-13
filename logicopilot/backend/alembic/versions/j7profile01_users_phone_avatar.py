"""Add users.phone and users.avatar_path - the Profile page's phone number and photo.

Both optional and self-service: each user edits their own on the Profile page (PATCH
/auth/me, POST/DELETE /auth/me/avatar). avatar_path is relative to settings.uploads_dir, e.g.
"avatars/<user id>-<unix time>.png" - the timestamp changes the URL on every upload, so a
browser never keeps showing the previous photo from its cache.

Revision ID: j7profile01
Revises: i6reqval01
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa

revision = "j7profile01"
down_revision = "i6reqval01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("avatar_path", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_path")
    op.drop_column("users", "phone")
