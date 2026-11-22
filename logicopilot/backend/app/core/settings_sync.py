"""Split servers only: pick up OpenAI key changes a Super Admin made on ANOTHER server.

The single OpenAI key override, the pooled keys and each tenant's "own keys" mode are loaded
into process memory, and reloaded only by the process that handled the save (system_settings /
openai_pool / tenants). With one process ("all", every server today) that is everything. Split,
the save lands on the WEB server while the AI calls run on the worker and ERP servers - which
would keep the old key until their next restart.

So on a split server a thread looks every CHECK_SECONDS at a small fingerprint of those rows and
reloads only when it changed. Not a blind periodic reload: refresh_pool() also forgets which
keys are cooling down after a rate limit, and that must not be thrown away every 30 seconds.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

logger = logging.getLogger(__name__)

CHECK_SECONDS = 30
_started = False
_lock = threading.Lock()


def fingerprint(db) -> str:
    from app.models.openai_api_key import OpenAIApiKey
    from app.models.tenant_extraction_config import TenantExtractionConfig
    from app.core.system_settings import get_system_settings

    parts = [get_system_settings(db).openai_api_key_encrypted or ""]
    parts += sorted(f"{r.id}:{bool(r.is_active)}:{r.sort_order}:{r.tenant_id}:{r.key_encrypted}"
                    for r in db.query(OpenAIApiKey).all())
    parts += sorted(f"own:{t}" for (t,) in db.query(TenantExtractionConfig.tenant_id)
                    .filter(TenantExtractionConfig.openai_key_mode == "own").all())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def reload_all(db) -> None:
    from app.core.openai_pool import refresh_pool, refresh_tenant_modes
    from app.core.system_settings import apply_openai_api_key_override

    apply_openai_api_key_override(db)
    refresh_pool(db)
    refresh_tenant_modes(db)


def check_once(db, last: str | None) -> str:
    """Returns the current fingerprint; reloads when it differs from `last` (None = startup,
    which main.py has already loaded - just remember it)."""
    now = fingerprint(db)
    if last is not None and now != last:
        reload_all(db)
        logger.warning("OpenAI key settings changed on another server - reloaded here")
    return now


def _loop() -> None:
    from app.db.session import SessionLocal

    last = None
    while True:
        db = SessionLocal()
        try:
            last = check_once(db, last)
        except Exception:  # noqa: BLE001 - never stop checking
            logger.exception("could not check the OpenAI key settings")
            db.rollback()
        finally:
            db.close()
        time.sleep(CHECK_SECONDS)


def start() -> None:
    """main.py, only when PROCESS_ROLE is split. Idempotent."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="settings-sync").start()
