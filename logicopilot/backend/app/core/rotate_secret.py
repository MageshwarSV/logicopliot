"""Rotate JWT_SECRET_KEY - and re-encrypt every secret stored with a key derived from it.

Why this exists: JWT_SECRET_KEY signs login tokens AND (see mail_crypto.py) is hashed into the
Fernet key every stored password/API key is encrypted with. A box that never set it runs on the
code's public default. Setting a new one is not enough on its own: every stored secret would
stop decrypting. This re-encrypts them, old key -> new key, in ONE transaction.

Run from the backend folder, with the backend STOPPED on every server of the environment
(a running process still holds the old key and would fail to read what this rewrote):

    NEW_JWT_SECRET_FILE=/root/cargora-secrets/jwt_secret venv/bin/python -m app.core.rotate_secret --check
    NEW_JWT_SECRET_FILE=/root/cargora-secrets/jwt_secret venv/bin/python -m app.core.rotate_secret --apply

then set JWT_SECRET_KEY=<the file's contents> in .env on EVERY server and start them. Everyone
signs in again (tokens signed with the old key no longer verify).

- Old key: OLD_JWT_SECRET if set, else whatever this process's settings say (.env or default).
- New key: read from the file in NEW_JWT_SECRET_FILE - never from argv, never printed.
- Idempotent: a value that already opens with the new key is left alone, so a second run
  after a half-finished rollout is safe.
- --apply refuses to write anything if any value opens with neither key.
- Prints counts only.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

# (table, primary key, encrypted column) - every place encrypt_secret() writes to.
COLUMNS = [
    ("users", "id", "mail_app_password_encrypted"),
    ("openai_api_keys", "id", "key_encrypted"),
    ("connected_mailboxes", "id", "app_password_encrypted"),
    ("system_settings", "id", "openai_api_key_encrypted"),
    ("system_settings", "id", "openai_admin_key_encrypted"),
    ("erp_branch_sessions", "id", "password_encrypted"),
    ("archive_passwords", "id", "password_encrypted"),
]


def fernet_for(secret: str) -> Fernet:
    """Same derivation as mail_crypto._fernet()."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def _opens(f: Fernet, token: str) -> str | None:
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def scan(db, old: Fernet, new: Fernet) -> list[dict]:
    """Per column: total, already on the new key, on the old key, unreadable. Writes nothing."""
    out = []
    for table, pk, col in COLUMNS:
        rows = db.execute(text(f"select {pk}, {col} from {table} where {col} is not null and {col} <> ''")).all()
        r = {"column": f"{table}.{col}", "total": len(rows), "new": 0, "old": 0, "unreadable": 0}
        for _id, tok in rows:
            if _opens(new, tok) is not None:
                r["new"] += 1
            elif _opens(old, tok) is not None:
                r["old"] += 1
            else:
                r["unreadable"] += 1
        out.append(r)
    return out


def apply(db, old: Fernet, new: Fernet) -> int:
    """Re-encrypt every old-key value with the new key, in the caller's transaction.
    Raises (writing nothing) if any value opens with neither key."""
    bad = [r for r in scan(db, old, new) if r["unreadable"]]
    if bad:
        raise RuntimeError("unreadable values - nothing changed: "
                           + ", ".join(f"{r['column']}={r['unreadable']}" for r in bad))
    changed = 0
    for table, pk, col in COLUMNS:
        rows = db.execute(text(f"select {pk}, {col} from {table} where {col} is not null and {col} <> ''")).all()
        for _id, tok in rows:
            if _opens(new, tok) is not None:
                continue
            plain = _opens(old, tok)
            db.execute(text(f"update {table} set {col} = :v where {pk} = :id"),
                       {"v": new.encrypt(plain.encode("utf-8")).decode("utf-8"), "id": _id})
            changed += 1
    return changed


def _keys() -> tuple[str, str]:
    from app.core.config import get_settings

    old = os.environ.get("OLD_JWT_SECRET") or get_settings().jwt_secret_key
    path = os.environ.get("NEW_JWT_SECRET_FILE", "")
    if not path or not os.path.isfile(path):
        sys.exit("NEW_JWT_SECRET_FILE must point at a file holding the new secret")
    new = open(path, encoding="utf-8").read().strip()
    if len(new) < 32:
        sys.exit("the new secret must be at least 32 characters")
    if new == old:
        sys.exit("the new secret is the same as the current one - nothing to rotate")
    return old, new


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if mode not in ("--check", "--apply"):
        print(__doc__)
        return 2
    import app.db.base  # noqa: F401 - register models
    from app.db.session import SessionLocal

    old_s, new_s = _keys()
    old, new = fernet_for(old_s), fernet_for(new_s)
    print("current key is the code default: "
          + ("YES" if old_s == "change-me-to-a-long-random-string" else "no"))
    db = SessionLocal()
    try:
        for r in scan(db, old, new):
            print(f"  {r['column']:<45} total {r['total']:>4} | old key {r['old']:>4} | "
                  f"new key {r['new']:>4} | unreadable {r['unreadable']:>3}")
        if mode == "--check":
            print("check only - nothing written")
            return 0
        n = apply(db, old, new)
        db.commit()
        print(f"re-encrypted {n} value(s) with the new key - now set JWT_SECRET_KEY on every "
              "server and start the backends")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"FAILED, nothing written: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
