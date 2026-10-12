"""A pool of OpenAI API keys, round-robinned across so one project's TPM/RPM ceiling never
blocks every call - see app/models/openai_api_key.py's docstring for why this only multiplies
real throughput when each key comes from a SEPARATE OpenAI Project.

Mirrors system_settings.py's own trick for the single key (apply_openai_api_key_override):
the pool is loaded into plain process memory ONCE (at startup, and again right after any
add/delete/toggle), so every existing OpenAI call site keeps working with no `db` session of
its own - get_openai_client() needs none at call time, exactly like the single-key path it
replaces. With no keys configured, get_openai_client() falls back to Settings.openai_api_key
(today's only behavior, unchanged) - this whole module is additive.
"""

import logging
import threading
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mail_crypto import decrypt_secret, encrypt_secret
from app.core.system_settings import InvalidOpenAIKey, _verify_openai_key
from app.models.openai_api_key import OpenAIApiKey

logger = logging.getLogger(__name__)

# How long a key that just hit a blocking error (quota exhausted, revoked, a rate limit with
# no self-correcting wait) is skipped before being offered again - long enough that a per-
# minute TPM cap has genuinely reset, short enough that a key fixed in the OpenAI dashboard
# (billing topped up, limit raised) is back in rotation within the same work session rather
# than needing another add/delete here to clear it.
_COOLDOWN_SECONDS = 120.0

_lock = threading.Lock()
# None = never loaded yet in this process; [] = loaded, deliberately empty (no pool configured,
# fall back to the single settings.openai_api_key). Each entry: (id, decrypted key).
_pool: list[tuple[str, str]] | None = None
_rr_index = 0
_cooldowns: dict[str, float] = {}  # key id -> monotonic() time it becomes usable again


def _load_pool(db: Session) -> list[tuple[str, str]]:
    rows = (
        db.query(OpenAIApiKey)
        .filter(OpenAIApiKey.is_active.is_(True))
        .order_by(OpenAIApiKey.sort_order, OpenAIApiKey.created_at)
        .all()
    )
    loaded = []
    for row in rows:
        try:
            loaded.append((row.id, decrypt_secret(row.key_encrypted)))
        except Exception:  # noqa: BLE001
            logger.exception("could not decrypt pooled OpenAI key %s (%s) - skipped", row.id, row.label)
    return loaded


def refresh_pool(db: Session) -> None:
    """Reloads the active pool from the database into process memory - called right after
    any add/delete/activate/deactivate, the same way apply_openai_api_key_override() is
    called right after the single key is saved."""
    global _pool, _rr_index
    with _lock:
        _pool = _load_pool(db)
        _rr_index = 0
        _cooldowns.clear()


def _ensure_loaded() -> list[tuple[str, str]]:
    """Mirrors apply_openai_api_key_override's own precedent exactly: that single-key path
    has no lazy DB fallback either - a process that never ran the startup hook just keeps
    using settings.openai_api_key from .env until its own next restart, not a correctness
    bug. Matching that here (rather than giving THIS path a stronger guarantee the single
    key never had) keeps every existing call site's "no db session needed to make an AI
    call" property true without exception - including in tests, which never run the startup
    hooks at all and must not start touching the database just because this module got
    imported."""
    return _pool or []


def mark_key_cooldown(key_id: str, *, seconds: float = _COOLDOWN_SECONDS) -> None:
    if key_id == "default":
        return  # the single Settings key has no sibling to fail over to - nothing to skip
    _cooldowns[key_id] = time.monotonic() + seconds
    logger.warning("OpenAI key %s put on cooldown for %.0fs after a blocking error", key_id, seconds)


def get_pool_size() -> int:
    """How many distinct keys a failover loop can try before giving up - the configured pool,
    or 1 for the single fallback key when no pool is configured."""
    return max(1, len(_ensure_loaded()))


def get_openai_client(*, timeout: float):
    """(client, key_id) - key_id is "default" when no pool is configured (today's only
    behavior: Settings.openai_api_key via the existing env-var override), otherwise the
    OpenAIApiKey.id of whichever pooled key this call should use.

    Round-robins through keys NOT currently cooling down, for a fair, even split of load
    across every project's own TPM/RPM budget. If every pooled key happens to be cooling
    down at once (every project rate-limited simultaneously - OpenAI itself having a bad
    moment, not this app's fault), picks whichever cools down soonest rather than refusing
    outright - a slightly-too-early retry is better than failing a call outright when there
    is nothing else to try."""
    from openai import OpenAI

    global _rr_index
    pool = _ensure_loaded()
    if not pool:
        settings = get_settings()
        return OpenAI(api_key=settings.openai_api_key, timeout=timeout), "default"

    now = time.monotonic()
    with _lock:
        live = [(kid, key) for kid, key in pool if _cooldowns.get(kid, 0.0) <= now]
        if not live:
            # Everything is cooling down - use whichever clears soonest.
            kid, key = min(pool, key=lambda item: _cooldowns.get(item[0], 0.0))
        else:
            _rr_index = _rr_index % len(live)
            kid, key = live[_rr_index]
            _rr_index += 1
    return OpenAI(api_key=key, timeout=timeout), kid


def add_api_key(db: Session, label: str, plaintext: str) -> OpenAIApiKey:
    label = (label or "").strip()
    plaintext = plaintext.strip()
    if not label:
        raise InvalidOpenAIKey("Give this key a label.")
    if not plaintext:
        raise InvalidOpenAIKey("An API key is required.")
    _verify_openai_key(plaintext)
    max_order = db.query(OpenAIApiKey).count()
    row = OpenAIApiKey(
        label=label, key_encrypted=encrypt_secret(plaintext), is_active=True, sort_order=max_order,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    refresh_pool(db)
    return row


def list_api_keys(db: Session) -> list[OpenAIApiKey]:
    return (
        db.query(OpenAIApiKey)
        .order_by(OpenAIApiKey.sort_order, OpenAIApiKey.created_at)
        .all()
    )


def set_api_key_active(db: Session, key_id: str, active: bool) -> OpenAIApiKey | None:
    row = db.get(OpenAIApiKey, key_id)
    if row is None:
        return None
    row.is_active = active
    db.commit()
    refresh_pool(db)
    return row


def delete_api_key(db: Session, key_id: str) -> bool:
    row = db.get(OpenAIApiKey, key_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    refresh_pool(db)
    return True
