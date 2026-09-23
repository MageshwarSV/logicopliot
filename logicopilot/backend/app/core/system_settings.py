"""A Super Admin's live on/off switches for AI-dependent work, and the OpenAI API key itself.

Read fresh from the database on every check - unlike app.core.config.Settings (env-based,
cached for the whole process lifetime via lru_cache), a Super Admin flipping a switch or
saving a new key from Settings needs it to take effect on the very next email pull or
extraction, with no restart.
"""

import logging
import os
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mail_crypto import decrypt_secret, encrypt_secret
from app.models.system_setting import SystemSetting

logger = logging.getLogger(__name__)


class InvalidOpenAIKey(Exception):
    """The key was rejected by OpenAI itself (checked live, the same way a mailbox app
    password is verified before being saved) - never saved, so a typo cannot silently
    replace a working key with a dead one."""


def get_system_settings(db: Session) -> SystemSetting:
    """The one global row, created on first use. Never per-tenant - this is a whole-system
    switch, deliberately: the AI provider being out of credits affects every tenant equally."""
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def is_email_pull_paused(db: Session) -> bool:
    return get_system_settings(db).email_pull_paused


def is_extraction_paused(db: Session) -> bool:
    return get_system_settings(db).extraction_paused


def has_custom_openai_key(db: Session) -> bool:
    """Whether a Super Admin has saved one from Settings - never any part of the key itself,
    not even masked. A partial value is still something "anyone can take", so the UI only
    ever gets a yes/no; changing it is a write-only action, same as an operator's own
    mailbox app password."""
    return bool(get_system_settings(db).openai_api_key_encrypted)


def _verify_openai_key(api_key: str) -> None:
    """A live, free check (listing models costs nothing and needs no credits) - the same
    principle as verifying a mailbox app password with a real IMAP login before saving it."""
    from openai import AuthenticationError, OpenAI

    try:
        OpenAI(api_key=api_key, timeout=15).models.list()
    except AuthenticationError as exc:
        raise InvalidOpenAIKey(str(exc)) from exc


def apply_openai_api_key_override(db: Session) -> None:
    """Puts whatever key is stored (if any) into effect for THIS process - called once at
    startup, and again right after a Super Admin saves a new one. get_settings() is
    lru_cache'd for the process lifetime, so changing os.environ alone would do nothing until
    the cache is cleared too; the next get_settings() call then rebuilds Settings() from the
    now-updated environment, which is how every existing OpenAI call site picks this up
    automatically - none of them needed to change.
    """
    row = get_system_settings(db)
    if not row.openai_api_key_encrypted:
        return
    try:
        key = decrypt_secret(row.openai_api_key_encrypted)
    except Exception:  # noqa: BLE001
        logger.exception("could not decrypt the stored OpenAI API key override")
        return
    os.environ["OPENAI_API_KEY"] = key
    get_settings.cache_clear()


def set_openai_api_key(db: Session, plaintext: str) -> None:
    """Verifies the key live, then encrypts, stores, and applies it for this process
    immediately - the very next AI call anywhere in the app uses it, no restart."""
    plaintext = plaintext.strip()
    if not plaintext:
        raise InvalidOpenAIKey("An API key is required.")
    _verify_openai_key(plaintext)
    row = get_system_settings(db)
    row.openai_api_key_encrypted = encrypt_secret(plaintext)
    db.commit()
    apply_openai_api_key_override(db)


class InvalidOpenAIAdminKey(Exception):
    """Rejected by OpenAI's organization endpoints when verified live - never saved, same
    principle as InvalidOpenAIKey above."""


def has_openai_admin_key(db: Session) -> bool:
    """Whether a Super Admin has connected one - write-only, same as the regular key."""
    return bool(get_system_settings(db).openai_admin_key_encrypted)


def get_openai_admin_key(db: Session) -> str | None:
    """The decrypted key, for Spend Analytics to call OpenAI with. None if never set, or if
    the stored value cannot be decrypted (logged, never raised - a broken key here should
    degrade Spend Analytics to its own local numbers, not break the page)."""
    row = get_system_settings(db)
    if not row.openai_admin_key_encrypted:
        return None
    try:
        return decrypt_secret(row.openai_admin_key_encrypted)
    except Exception:  # noqa: BLE001
        logger.exception("could not decrypt the stored OpenAI Admin API key")
        return None


def _verify_openai_admin_key(api_key: str) -> None:
    """A tiny real call (today's cost, one bucket) - the only way to know a key actually has
    organization usage-read permission, since a regular API key looks identical until used
    against this specific endpoint."""
    from datetime import date as _date

    from app.core.openai_admin import OpenAIAdminAPIError, fetch_daily_costs

    try:
        fetch_daily_costs(api_key, _date.today(), _date.today())
    except OpenAIAdminAPIError as exc:
        raise InvalidOpenAIAdminKey(str(exc)) from exc


def set_openai_admin_key(db: Session, plaintext: str) -> None:
    """Verifies the key live against OpenAI's own costs endpoint, then encrypts and stores
    it. Never applied as the OPENAI_API_KEY override - this key is for reading spend, never
    for making calls, and must never be confused with (or overwrite) the regular key."""
    plaintext = plaintext.strip()
    if not plaintext:
        raise InvalidOpenAIAdminKey("An Admin API key is required.")
    _verify_openai_admin_key(plaintext)
    row = get_system_settings(db)
    row.openai_admin_key_encrypted = encrypt_secret(plaintext)
    db.commit()


def clear_openai_admin_key(db: Session) -> None:
    row = get_system_settings(db)
    row.openai_admin_key_encrypted = None
    db.commit()


class InvalidWorkerCount(Exception):
    """Outside 1..max_email_poll_workers() - never saved, so a number this server cannot
    actually sustain never takes effect."""


def max_email_poll_workers() -> int:
    """A hard ceiling tied to what THIS server can actually do, not a guess - one thread per
    CPU core. Mailbox reads are network/IO-bound rather than CPU-bound, so this is already
    generous, not a tight technical limit."""
    return max(1, os.cpu_count() or 1)


def get_email_poll_workers(db: Session) -> int:
    return get_system_settings(db).email_poll_workers


def set_email_poll_workers(db: Session, count: int) -> None:
    cap = max_email_poll_workers()
    if count < 1 or count > cap:
        raise InvalidWorkerCount(
            f"This server can run at most {cap} at once (one per CPU core); {count} was refused.")
    row = get_system_settings(db)
    row.email_poll_workers = count
    db.commit()


class InvalidOpenAIBalance(Exception):
    """A negative balance was rejected - never saved."""


def set_openai_balance(db: Session, balance_usd: float | None, expiry: date | None) -> None:
    """Manually entered by a Super Admin (see app/core/currency.py's module docstring for
    why this cannot come from an API). balance_usd=None clears it back to "not set"."""
    if balance_usd is not None and balance_usd < 0:
        raise InvalidOpenAIBalance("Balance cannot be negative.")
    row = get_system_settings(db)
    row.openai_balance_usd = balance_usd
    row.openai_balance_expiry = expiry
    db.commit()
