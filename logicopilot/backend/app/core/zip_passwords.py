"""Saved zip passwords (ArchivePassword): looked up for a locked zip, remembered when an operator
types one with "remember for this customer".

Only one customer (Flex) uses password zips today, always one of two fixed passwords - but this
is per tenant and per label, so any other customer is just more rows on the "Zip passwords"
screen. Passwords are never logged and never returned by the API.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.mail_crypto import decrypt_secret, encrypt_secret
from app.models.archive_password import ArchivePassword

logger = logging.getLogger(__name__)

# The job's badge while it waits for a zip password before anything is read (Job.stage_override).
WAITING_FOR_PASSWORD = "Waiting for Password"
MAX_TRIES = 20


def _domain(sender: str | None) -> str:
    return (sender or "").rsplit("@", 1)[-1].strip().lower()


def candidate_passwords(db: Session, tenant_id: str, *, group_id: str | None = None,
                        sender: str | None = None) -> list[tuple[str, str]]:
    """[(row id, password)] to try, best first: rows narrowed to this template or this sender's
    domain, then every other row of the tenant - most recently used first within each."""
    rows = db.query(ArchivePassword).filter(ArchivePassword.tenant_id == tenant_id).all()
    dom = _domain(sender)

    def matches(r: ArchivePassword) -> bool:
        if r.group_id and group_id and r.group_id == group_id:
            return True
        return bool(dom and any(dom == (d or "").strip().lower().lstrip("@") for d in (r.sender_domains or [])))

    def recency(r: ArchivePassword) -> float:
        return r.last_used_at.timestamp() if r.last_used_at else 0.0

    ordered = sorted(rows, key=lambda r: (not matches(r), -recency(r)))
    out: list[tuple[str, str]] = []
    for r in ordered[:MAX_TRIES]:
        try:
            out.append((r.id, decrypt_secret(r.password_encrypted)))
        except Exception:  # noqa: BLE001 - a row the current key cannot read is simply skipped
            logger.warning("zip password %s could not be decrypted - skipped", r.id)
    return out


def mark_used(db: Session, row_ids: list[str]) -> None:
    now = datetime.now(timezone.utc)
    for r in db.query(ArchivePassword).filter(ArchivePassword.id.in_(row_ids or [""])).all():
        r.last_used_at = now
        r.use_count = (r.use_count or 0) + 1


def save_password(db: Session, *, tenant_id: str, label: str, password: str,
                  group_id: str | None = None, sender_domains: list[str] | None = None,
                  created_by_id: str | None = None) -> ArchivePassword:
    """Add a password for this tenant - or return the existing row if it is already saved."""
    for row_id, existing in candidate_passwords(db, tenant_id):
        if existing == password:
            return db.get(ArchivePassword, row_id)
    row = ArchivePassword(tenant_id=tenant_id, label=(label or "Zip password").strip()[:120],
                          password_encrypted=encrypt_secret(password), group_id=group_id,
                          sender_domains=[d.strip().lower().lstrip("@") for d in (sender_domains or []) if d.strip()] or None,
                          created_by_id=created_by_id)
    db.add(row)
    db.flush()
    return row
