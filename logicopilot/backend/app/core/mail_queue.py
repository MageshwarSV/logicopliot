"""The mail queue (4-server plan, step P1): the background poller FINDS mail, the workers READ it.

With MAIL_QUEUE off (the default, and every server today) none of this runs: the poller still
processes every mail inside its own cycle, exactly as before.

On, the poller's cycle only harvests (IMAP search + PEEK fetch, seconds) and, per mail:
  - saves the raw .eml under uploads/_mailq/ (the shared folder on a split setup),
  - pushes a small JSON item to Redis - mail:queue, or mail:large for a big zip on a
    large-job-lane template (one at a time, as the lane always did).
Mail workers take items oldest first and run THE SAME _process_one_message the cycle used to:
claims, duplicate checks, OCR, classification, placement and extraction are all unchanged.

Durability, the same pattern as the ERP queue (erp_redis_queue.py): a worker RESERVES an item
(BRPOPLPUSH onto a processing list) and removes it only when the mail is done, so a restart or
deploy mid-mail leaves it on the processing list, and startup puts it back - ahead of anything
waiting.

Not queued twice: the mail stays unread in the inbox (PEEK), so the next minute's poll finds it
again. mail:keys (Message-ID -> queued at) stops a second item while the first is waiting or
running; an entry older than DUP_TTL is ignored, so a lost one cannot block a mail for ever.

Kept in the cycle (not queued): a manual "Pull email now" / re-examine (the person waits for
the result) and the per-shipment parts of a split pre-alert (their parent is settled when all
of them are done, in that same cycle).
"""
from __future__ import annotations

import email
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUE_KEY = "mail:queue"
PROCESSING_KEY = "mail:processing"
LARGE_KEY = "mail:large"
LARGE_PROCESSING_KEY = "mail:large:processing"
KEYS_HASH = "mail:keys"
DUP_TTL = 6 * 3600
_POP_TIMEOUT = 5              # below erp_redis_queue's socket timeout (pop + 10)

# Same atomic "back to the front" step as the ERP queue: newest-reserved first onto the right
# end, so the oldest interrupted mail ends up right-most = taken first.
_REQUEUE_ONE = """
local v = redis.call('LPOP', KEYS[1])
if v then redis.call('RPUSH', KEYS[2], v) end
return v
"""

_stop = threading.Event()
_workers: list[threading.Thread] = []
_lock = threading.Lock()


def _client():
    from app.core.erp_redis_queue import _redis

    return _redis()


def enabled() -> bool:
    """MAIL_QUEUE on, this process reads mail (PROCESS_ROLE all/worker), and Redis answers."""
    from app.core.config import get_settings
    from app.core.process_role import runs

    return bool(getattr(get_settings(), "mail_queue", False)) and runs("mail") and _client() is not None


def _eml_dir() -> Path:
    from app.core.config import get_settings

    d = Path(get_settings().uploads_dir) / "_mailq"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mid_of(raw: bytes) -> str:
    from app.core.email_puller import _message_id

    return _message_id(email.message_from_bytes(raw))


def enqueue(task: dict, *, reexamine: bool = False, large: bool = False) -> bool:
    """Queue one harvested mail. False = it is already waiting or running (not queued again).
    Raises if it could not be queued - the caller then processes it in the cycle instead."""
    client = _client()
    raw: bytes = task["raw"]
    mid = _mid_of(raw)
    now = time.time()
    if not client.hsetnx(KEYS_HASH, mid, str(now)):
        try:
            age = now - float(client.hget(KEYS_HASH, mid) or 0)
        except (TypeError, ValueError):
            age = DUP_TTL + 1
        if age < DUP_TTL:
            return False
        client.hset(KEYS_HASH, mid, str(now))          # stale marker: a lost item, queue again
    name = hashlib.sha256(mid.encode()).hexdigest()[:32] + ".eml"
    path = _eml_dir() / name
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    uid = task.get("uid")
    src = task.get("src") or {}
    item = {
        "eml": name, "mid": mid, "queued_at": now,
        "uid": uid.decode() if isinstance(uid, (bytes, bytearray)) else uid,
        "mailbox_id": src.get("mailbox_id"), "tenant_id": src.get("tenant_id"),
        "group_id": src.get("group_id"), "reexamine": bool(reexamine),
        "split_checked": task.get("split_checked") is True,
        "prealert_rec": task.get("prealert_rec") or None,
    }
    try:
        client.lpush(LARGE_KEY if large else QUEUE_KEY, json.dumps(item, default=str))
    except Exception:
        client.hdel(KEYS_HASH, mid)
        raise
    return True


def _source_for(db, item: dict) -> dict | None:
    """The mailbox the mail came from, with its credentials, re-read now (never stored in Redis)."""
    from app.core.config import get_settings
    from app.core.email_puller import _app_password, _connected_mailboxes

    mb_id = item.get("mailbox_id")
    if mb_id is None:
        st = get_settings()
        if not (st.gmail_user and _app_password()):
            return None
        return {"mailbox_id": None, "email": None, "password": None, "host": None,
                "tenant_id": item.get("tenant_id"), "group_id": None}
    return next((s for s in _connected_mailboxes(db) if s["mailbox_id"] == mb_id), None)


def run_item(item: dict) -> dict | None:
    """Process one queued mail exactly as the cycle would have. Always clears its markers."""
    from app.core.custom_page_filter import get_active_custom_filter_texts
    from app.core.config import get_settings
    from app.core.email_puller import _mailbox_setup, _process_one_message
    from app.core.system_settings import get_extraction_engine
    from app.db.session import SessionLocal

    path = _eml_dir() / item["eml"]
    db = SessionLocal()
    try:
        if not path.exists():
            logger.warning("mail queue: %s is gone - skipped (the next poll finds the mail again)",
                           item.get("mid", "")[:60])
            return None
        src = _source_for(db, item)
        if src is None:
            logger.info("mail queue: mailbox for %s is no longer connected/active - skipped",
                        item.get("mid", "")[:60])
            return None
        ctx, early = _mailbox_setup(db, src["tenant_id"], src["group_id"])
        if early is not None:
            return early
        uid = item.get("uid")
        return _process_one_message(
            db, uid.encode() if isinstance(uid, str) else uid, path.read_bytes(), ctx,
            src["email"], src["password"], src["host"],
            get_extraction_engine(db), get_settings().vision_engine_model,
            get_active_custom_filter_texts(db), bool(item.get("reexamine")),
            **({"split_checked": True} if item.get("split_checked") else {}),
            **({"prealert_rec": item["prealert_rec"]} if item.get("prealert_rec") else {}),
        )
    finally:
        db.close()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            _client().hdel(KEYS_HASH, item.get("mid", ""))
        except Exception:  # noqa: BLE001
            logger.warning("mail queue: could not clear the queued marker", exc_info=True)


def _worker(name: str, src_key: str, proc_key: str, large: bool) -> None:
    logger.info("mail queue worker %s started", name)
    while not _stop.is_set():
        client = _client()
        if client is None:
            _stop.wait(30)
            continue
        try:
            raw = client.brpoplpush(src_key, proc_key, timeout=_POP_TIMEOUT)
        except Exception:  # noqa: BLE001 - a dropped connection must not kill the worker
            logger.warning("mail queue worker %s could not read the queue", name, exc_info=True)
            _stop.wait(5)
            continue
        if raw is None:
            continue
        started = time.monotonic()
        try:
            item = json.loads(raw)
            logger.info("mail queue worker %s: reading %s", name, item.get("mid", "")[:70])
            if large:
                # A big zip runs as a large-job-lane item: its own parallel-call settings apply.
                from app.core import large_job_lane

                large_job_lane._tl.active = True
                try:
                    run_item(item)
                finally:
                    large_job_lane._tl.active = False
            else:
                run_item(item)
        except Exception:  # noqa: BLE001 - one bad mail must never stop the worker
            logger.exception("mail queue worker %s failed on %s", name, str(raw)[:200])
        finally:
            try:
                client.lrem(proc_key, 1, raw)
            except Exception:  # noqa: BLE001
                logger.warning("could not clear a finished mail off %s", proc_key, exc_info=True)
            logger.info("mail queue worker %s: done in %.1fs", name, time.monotonic() - started)
    logger.info("mail queue worker %s stopped", name)


def requeue_orphans() -> int:
    """Startup only: put every mail a dead process was reading back at the front."""
    client = _client()
    if client is None:
        return 0
    moved = 0
    for proc_key, src_key in ((PROCESSING_KEY, QUEUE_KEY), (LARGE_PROCESSING_KEY, LARGE_KEY)):
        try:
            while client.eval(_REQUEUE_ONE, 2, proc_key, src_key) is not None:
                moved += 1
                if moved > 5000:
                    logger.error("mail queue: stopped requeueing at 5000 - check %s", proc_key)
                    return moved
        except Exception:  # noqa: BLE001
            logger.warning("mail queue: could not requeue %s", proc_key, exc_info=True)
    if moved:
        logger.warning("mail queue: %d mail(s) interrupted by a restart put back first", moved)
    return moved


def start(workers: int) -> None:
    """Mail-reading process only (main.py). Requeue the interrupted, then keep `workers` normal
    workers and ONE large-mail worker running. Idempotent; re-called it tops up dead workers."""
    global _workers
    if not enabled():
        return
    with _lock:
        if not _workers:
            requeue_orphans()
        _stop.clear()
        _workers = [t for t in _workers if t.is_alive()]
        have = {t.name for t in _workers}
        want = [(f"mail-q{i + 1}", QUEUE_KEY, PROCESSING_KEY, False) for i in range(max(1, workers))]
        want.append(("mail-large", LARGE_KEY, LARGE_PROCESSING_KEY, True))
        for name, src_key, proc_key, large in want:
            if name not in have:
                t = threading.Thread(target=_worker, args=(name, src_key, proc_key, large),
                                     name=name, daemon=True)
                t.start()
                _workers.append(t)


def stop() -> None:
    _stop.set()


def depth() -> dict:
    client = _client()
    if client is None:
        return {"enabled": False}
    try:
        return {"enabled": enabled(),
                "waiting": client.llen(QUEUE_KEY), "running": client.llen(PROCESSING_KEY),
                "large_waiting": client.llen(LARGE_KEY),
                "large_running": client.llen(LARGE_PROCESSING_KEY)}
    except Exception:  # noqa: BLE001
        return {"enabled": enabled(), "waiting": None, "running": None}
