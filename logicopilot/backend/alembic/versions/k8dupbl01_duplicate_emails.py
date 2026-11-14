"""The duplicate-BL queue, and the BL number on every email we have already seen.

Two things:

- `email_seen.bl_number` - the Bill of Lading read off each message's SUBJECT. This is what
  makes the duplicate check possible AT ALL without opening a single attachment: one indexed
  lookup on (bl_number, sender) instead of OCR-ing the mail to find out what it is about.
  Backfilled for the messages already recorded, so the check works from the first poll after
  this ships rather than only against mail that arrives afterwards.

- `duplicate_emails` - the queue a repeated pre-alert lands in instead of becoming a job.

Revision ID: k8dupbl01
Revises: j7profile01
Create Date: 2026-11-14
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "k8dupbl01"
down_revision = "j7profile01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_seen", sa.Column("bl_number", sa.String(length=64), nullable=True))
    op.create_index("ix_email_seen_bl_number", "email_seen", ["bl_number"])

    op.create_table(
        "duplicate_emails",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("tenant_id", sa.String(length=36),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("group_id", sa.String(length=36),
                  sa.ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sender", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bl_number", sa.String(length=64), nullable=False),
        sa.Column("original_job_id", sa.String(length=36),
                  sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("original_reference", sa.String(length=64), nullable=True),
        sa.Column("original_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("processed_job_id", sa.String(length=36),
                  sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("handled_by_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_duplicate_emails_message_id", "duplicate_emails", ["message_id"],
                    unique=True)
    op.create_index("ix_duplicate_emails_bl_number", "duplicate_emails", ["bl_number"])
    op.create_index("ix_duplicate_emails_tenant_id", "duplicate_emails", ["tenant_id"])
    op.create_index("ix_duplicate_emails_sender", "duplicate_emails", ["sender"])

    _backfill_bl_numbers()


def _backfill_bl_numbers() -> None:
    """Read the BL off the subject of every message already recorded.

    Done here rather than left to run forward only, so the very first poll after this ships
    can already recognise a repeat of something that arrived last week. Deliberately a plain
    copy of app/core/email_bl.py's patterns rather than an import of it: a migration has to
    keep doing what it did on the day it ran, and that module will keep being tuned.
    """
    label = (r"(?:HBL\s*(?:NO|NUMBER)?|HOUSE\s*B/?L(?:\s*NO)?|HB\s*#|CBL"
             r"|B/?L\s*(?:NO|NUMBER)|B/?L)")
    bl = re.compile(rf"\b{label}\s*[:\-#]?\s*([A-Z0-9][A-Z0-9-]{{4,24}})", re.IGNORECASE)
    master = re.compile(r"(?:MB\s*#|MBL|MAWB|MASTER\s*B/?L)\s*(?:NO|NUMBER)?\s*[:\-]?\s*$",
                        re.IGNORECASE)
    never = {"NUMBER", "DOCUMENT", "SHIPPER", "DETAILS", "ATTACHED", "PREALERT",
             "ALERT", "MOOWR", "NOTICE", "COPY", "DRAFT", "FINAL"}

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, subject FROM email_seen WHERE subject IS NOT NULL AND subject <> ''"
    )).fetchall()
    updates = []
    for row_id, subject in rows:
        text = " ".join((subject or "").split())
        found = None
        for m in bl.finditer(text):
            if master.search(text[max(0, m.start() - 24): m.start()]):
                continue
            raw = m.group(1).strip(" -/:#")
            if raw.upper() in never:
                continue
            key = re.sub(r"[^A-Z0-9]+", "", raw.upper())
            if len(key) >= 5 and any(c.isdigit() for c in key):
                found = key
                break
        if found:
            updates.append({"i": row_id, "b": found})
    if updates:
        conn.execute(sa.text("UPDATE email_seen SET bl_number = :b WHERE id = :i"), updates)


def downgrade() -> None:
    op.drop_index("ix_duplicate_emails_sender", table_name="duplicate_emails")
    op.drop_index("ix_duplicate_emails_tenant_id", table_name="duplicate_emails")
    op.drop_index("ix_duplicate_emails_bl_number", table_name="duplicate_emails")
    op.drop_index("ix_duplicate_emails_message_id", table_name="duplicate_emails")
    op.drop_table("duplicate_emails")
    op.drop_index("ix_email_seen_bl_number", table_name="email_seen")
    op.drop_column("email_seen", "bl_number")
