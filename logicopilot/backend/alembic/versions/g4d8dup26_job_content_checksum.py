"""job content checksum — jobs.content_checksum, jobs.duplicate_of_job_id

A fingerprint of what a job's extracted documents actually say, computed once extraction
finishes. Lets a re-sent email (different Message-ID, different filename, even a re-scan)
be recognised as the same shipment instead of silently creating a second job.

Also backfills the checksum for every job extracted BEFORE this column existed - otherwise
a new job resending an old, already-completed job's paperwork would never be recognised as a
duplicate, since the old job's checksum would sit NULL forever and never match anything.

Revision ID: g4d8dup26
Revises: f3c7mailhost25
"""
from alembic import op
import sqlalchemy as sa

revision = "g4d8dup26"
down_revision = "f3c7mailhost25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("content_checksum", sa.String(length=64), nullable=True))
    op.create_index("ix_jobs_content_checksum", "jobs", ["content_checksum"])
    op.add_column("jobs", sa.Column("duplicate_of_job_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_jobs_duplicate_of_job_id", "jobs", "jobs",
        ["duplicate_of_job_id"], ["id"], ondelete="SET NULL",
    )
    _backfill_checksums()


def _backfill_checksums() -> None:
    """Frozen copy of _compute_content_checksum's exact hashing logic (app/api/v1/jobs.py at
    the time this migration was written) - a migration must not import application code,
    which is free to change shape long after this migration has already run in production.

    Only SETS a checksum on every already-extracted job; never touches status or flags
    anything as a duplicate retroactively. A job already Completed, Failed or mid-ERP-entry
    must not have its status rewritten by a data backfill - the point is only to make
    history COMPARABLE, so the next genuinely new job can be checked against it.
    """
    import hashlib
    import json

    conn = op.get_bind()
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String),
        sa.column("extracted_keyouted_data", sa.JSON),
        sa.column("content_checksum", sa.String),
    )
    rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.extracted_keyouted_data)
        .where(jobs.c.extracted_keyouted_data.isnot(None))
    ).fetchall()
    updated = 0
    for job_id, keyouted in rows:
        if not keyouted:
            continue
        # The JSON column comes back already deserialized on Postgres/psycopg2; guard the
        # string case too so this is not silently dialect-specific.
        if isinstance(keyouted, str):
            try:
                keyouted = json.loads(keyouted)
            except (TypeError, ValueError):
                continue
        parts = []
        for bucket, fields in keyouted.items():
            for label, value in fields.items():
                text = ("|".join(str(v or "").strip().lower() for v in value)
                        if isinstance(value, list) else str(value or "").strip().lower())
                if text:
                    parts.append(f"{bucket}::{label}::{text}")
        if not parts:
            continue
        checksum = hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()
        conn.execute(
            jobs.update().where(jobs.c.id == job_id).values(content_checksum=checksum)
        )
        updated += 1
    print(f"g4d8dup26: backfilled content_checksum on {updated} already-extracted job(s)")


def downgrade() -> None:
    op.drop_constraint("fk_jobs_duplicate_of_job_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "duplicate_of_job_id")
    op.drop_index("ix_jobs_content_checksum", table_name="jobs")
    op.drop_column("jobs", "content_checksum")
