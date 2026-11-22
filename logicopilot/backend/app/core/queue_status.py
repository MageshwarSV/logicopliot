"""Queue visibility (4-server plan, step P3): what every queue holds and which server works it.

Every queue lives in Redis, so the website (any server) can read their lengths directly. What
it cannot see on its own is the WORKERS - they run on other servers on a split setup. So every
process that runs background work writes a small heartbeat (HEARTBEAT_KEY:<host>:<role>, kept
for HEARTBEAT_TTL seconds and refreshed every HEARTBEAT_EVERY) saying which server it is, its
role and how many workers of each kind it runs. A server that stops sending one simply drops
off the list - which is itself the signal that it is down.

snapshot() is what Platform Settings shows: each queue's waiting / running / oldest-waiting age,
a warning when the oldest item has waited longer than STALE_AFTER_SECONDS, and the servers.
Read-only; nothing here changes any queue.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "proc"
HEARTBEAT_EVERY = 30
HEARTBEAT_TTL = 120
STALE_AFTER_SECONDS = 10 * 60

_started = False
_lock = threading.Lock()


def _client():
    from app.core.erp_redis_queue import _redis

    return _redis()


def _workers_alive(prefix: str) -> int:
    return sum(1 for t in threading.enumerate() if t.name.startswith(prefix) and t.is_alive())


def _beat_payload() -> dict:
    from app.core import extract_queue, mail_queue
    from app.core.config import get_settings
    from app.core.process_role import role

    st = get_settings()
    return {
        "host": socket.gethostname(), "role": role(), "at": time.time(),
        "mail_queue_on": mail_queue.enabled(), "extract_queue_on": extract_queue.enabled(),
        "mail_workers": _workers_alive("mail-q") + _workers_alive("mail-large"),
        "extract_workers": _workers_alive("extract-q"),
        "erp_workers": _workers_alive("erp-q"),
        "mail_polling": int(getattr(st, "email_poll_minutes", 0) or 0) > 0,
        "ocr_engine": getattr(st, "ocr_engine", "docai"),
    }


def beat_once() -> None:
    client = _client()
    if client is None:
        return
    p = _beat_payload()
    client.set(f"{HEARTBEAT_KEY}:{p['host']}:{p['role']}", json.dumps(p), ex=HEARTBEAT_TTL)


def start_heartbeat() -> None:
    """main.py, every process (needs Redis). Idempotent."""
    global _started
    with _lock:
        if _started or _client() is None:
            return
        _started = True

    def _loop() -> None:
        time.sleep(10)          # let the workers start first, so the first beat counts them
        while True:
            try:
                beat_once()
            except Exception:  # noqa: BLE001 - visibility must never hurt the work
                logger.debug("queue heartbeat failed", exc_info=True)
            time.sleep(HEARTBEAT_EVERY)

    threading.Thread(target=_loop, daemon=True, name="queue-heartbeat").start()


def _oldest_age(client, key: str, now: float) -> float | None:
    """Age in seconds of the item that has waited longest (workers take from the right end)."""
    try:
        raw = client.lindex(key, -1)
        if raw is None:
            return None
        queued = json.loads(raw).get("queued_at")
        return max(0.0, now - float(queued)) if queued else None
    except Exception:  # noqa: BLE001
        return None


def _queue(client, name: str, label: str, wait_key: str, run_key: str, now: float) -> dict:
    waiting = client.llen(wait_key)
    running = client.llen(run_key)
    age = _oldest_age(client, wait_key, now) if waiting else None
    return {"name": name, "label": label, "waiting": waiting, "running": running,
            "oldest_wait_seconds": round(age) if age is not None else None,
            "stale": bool(age is not None and age > STALE_AFTER_SECONDS)}


def snapshot() -> dict:
    from app.core import erp_redis_queue, extract_queue, mail_queue

    client = _client()
    if client is None:
        return {"redis": False, "queues": [], "servers": [], "warnings": []}
    now = time.time()
    queues = [
        _queue(client, "mail", "Mail", mail_queue.QUEUE_KEY, mail_queue.PROCESSING_KEY, now),
        _queue(client, "mail_large", "Large mail", mail_queue.LARGE_KEY,
               mail_queue.LARGE_PROCESSING_KEY, now),
        _queue(client, "extract", "Extraction", extract_queue.QUEUE_KEY,
               extract_queue.PROCESSING_KEY, now),
        _queue(client, "erp", "ERP entry", erp_redis_queue.QUEUE_KEY,
               erp_redis_queue.PROCESSING_KEY, now),
    ]
    servers = []
    for key in client.scan_iter(match=f"{HEARTBEAT_KEY}:*", count=100):
        try:
            p = json.loads(client.get(key) or "{}")
        except Exception:  # noqa: BLE001
            continue
        if p:
            p["seen_seconds_ago"] = round(max(0.0, now - float(p.get("at") or now)))
            servers.append(p)
    servers.sort(key=lambda p: (p.get("host") or "", p.get("role") or ""))
    warnings = [f"{q['label']}: the oldest item has waited {q['oldest_wait_seconds'] // 60} min"
                for q in queues if q["stale"]]
    # A queue with items but no live worker anywhere is stuck, whatever its age.
    worker_field = {"mail": "mail_workers", "mail_large": "mail_workers",
                    "extract": "extract_workers", "erp": "erp_workers"}
    for q in queues:
        if q["waiting"] and servers and not any(s.get(worker_field[q["name"]]) for s in servers):
            warnings.append(f"{q['label']}: {q['waiting']} waiting but no server is running its workers")
    return {"redis": True, "queues": queues, "servers": servers, "warnings": warnings,
            "stale_after_seconds": STALE_AFTER_SECONDS}
