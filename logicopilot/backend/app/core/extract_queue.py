"""The extraction queue (4-server plan, step P2): every extraction is a Redis queue item.

With EXTRACT_QUEUE off (the default, and every server today) none of this runs: extractions
start in-process exactly as before (or through the split doorbell, extraction_dispatch.py).

On, every way an extraction starts - auto-extract after a mail's documents are placed, the
Extract / Run extraction / Re-run buttons, a one-document re-read, the restart resume - puts a
small item on extract:queue. Workers on the extracting process (PROCESS_ROLE all/worker) take
items oldest first and run the same extraction (jobs._run_extraction_job).

The shared cap is kept: a worker runs its item THROUGH the existing in-process pool
(extraction_queue.run_queued), so classification and extraction still share one limit - the
protection added after the 2026-11-03 outage - and the queue only decides the order and
survives restarts.

Durable like the mail and ERP queues: an item is RESERVED (BRPOPLPUSH -> extract:processing)
until the run ends; startup puts interrupted runs back first. extract:keys (job id -> queued
at) stops a second item for a job already waiting or running. The database stays the safety
net: restart_resume still finds every job left "extracting" and queues it (a no-op for one
already put back).

Large-job-lane jobs are unchanged: they keep their own one-at-a-time lane.
"""
from __future__ import annotations

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

QUEUE_KEY = "extract:queue"
PROCESSING_KEY = "extract:processing"
KEYS_HASH = "extract:keys"
DUP_TTL = 2 * 3600
_POP_TIMEOUT = 5

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
    """EXTRACT_QUEUE on and Redis answers. Any process may queue; only the extracting process
    runs workers (start())."""
    from app.core.config import get_settings

    return bool(getattr(get_settings(), "extract_queue", False)) and _client() is not None


def enqueue(job_id: str, only_job_document_id: str | None = None, is_rerun: bool = False) -> bool:
    """Queue one extraction. False = this job is already waiting or running. Raises if it could
    not be queued (the caller then runs it the old way)."""
    client = _client()
    now = time.time()
    if not client.hsetnx(KEYS_HASH, job_id, str(now)):
        try:
            age = now - float(client.hget(KEYS_HASH, job_id) or 0)
        except (TypeError, ValueError):
            age = DUP_TTL + 1
        if age < DUP_TTL:
            return False
        client.hset(KEYS_HASH, job_id, str(now))
    item = {"job_id": job_id, "doc": only_job_document_id, "is_rerun": bool(is_rerun),
            "queued_at": now}
    try:
        client.lpush(QUEUE_KEY, json.dumps(item))
    except Exception:
        client.hdel(KEYS_HASH, job_id)
        raise
    logger.info("job %s: extraction queued%s", job_id,
                f" (one document {only_job_document_id})" if only_job_document_id else "")
    return True


def run_item(item: dict) -> None:
    """Run one queued extraction - only if the job still waits for it - then clear its marker."""
    from app.api.v1.jobs import _run_extraction_job
    from app.core import extraction_dispatch
    from app.core.extraction_queue import run_queued
    from app.db.session import SessionLocal
    from app.models.job import Job

    job_id = item["job_id"]
    try:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            status = job.status if job is not None else None
        finally:
            db.close()
        if status != "extracting":
            logger.info("extraction queue: job %s is %s now - nothing to run", job_id, status)
            return
        if not extraction_dispatch.claim(job_id):
            logger.info("extraction queue: job %s is already running here - skipped", job_id)
            return
        try:
            run_queued(lambda: _run_extraction_job(job_id, item.get("doc") or None,
                                                   is_rerun=bool(item.get("is_rerun"))))
        finally:
            extraction_dispatch.release(job_id)
    finally:
        try:
            _client().hdel(KEYS_HASH, job_id)
        except Exception:  # noqa: BLE001
            logger.warning("extraction queue: could not clear the queued marker", exc_info=True)


def _worker(name: str) -> None:
    logger.info("extraction queue worker %s started", name)
    while not _stop.is_set():
        client = _client()
        if client is None:
            _stop.wait(30)
            continue
        try:
            raw = client.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=_POP_TIMEOUT)
        except Exception:  # noqa: BLE001
            logger.warning("extraction queue worker %s could not read the queue", name, exc_info=True)
            _stop.wait(5)
            continue
        if raw is None:
            continue
        started = time.monotonic()
        job_id = "?"
        try:
            item = json.loads(raw)
            job_id = item.get("job_id", "?")
            logger.info("extraction queue worker %s: job %s", name, job_id)
            run_item(item)
        except Exception:  # noqa: BLE001 - one bad item must never stop the worker
            logger.exception("extraction queue worker %s failed on %s", name, str(raw)[:200])
        finally:
            try:
                client.lrem(PROCESSING_KEY, 1, raw)
            except Exception:  # noqa: BLE001
                logger.warning("could not clear a finished extraction off %s", PROCESSING_KEY,
                               exc_info=True)
            logger.info("extraction queue worker %s: job %s done in %.1fs", name, job_id,
                        time.monotonic() - started)
    logger.info("extraction queue worker %s stopped", name)


def requeue_orphans() -> int:
    """Startup only: every extraction a dead process was running goes back to the front."""
    client = _client()
    if client is None:
        return 0
    moved = 0
    try:
        while client.eval(_REQUEUE_ONE, 2, PROCESSING_KEY, QUEUE_KEY) is not None:
            moved += 1
            if moved > 5000:
                logger.error("extraction queue: stopped requeueing at 5000 - check %s", PROCESSING_KEY)
                break
    except Exception:  # noqa: BLE001
        logger.warning("extraction queue: could not requeue interrupted runs", exc_info=True)
    if moved:
        logger.warning("extraction queue: %d run(s) interrupted by a restart put back first", moved)
    return moved


def start() -> None:
    """Extracting process only (main.py). As many workers as the shared pool has slots."""
    global _workers
    from app.core.extraction_queue import _worker_count
    from app.core.process_role import runs

    if not (enabled() and runs("extraction")):
        return
    with _lock:
        if not _workers:
            requeue_orphans()
        _stop.clear()
        _workers = [t for t in _workers if t.is_alive()]
        have = {t.name for t in _workers}
        for i in range(_worker_count()):
            name = f"extract-q{i + 1}"
            if name not in have:
                t = threading.Thread(target=_worker, args=(name,), name=name, daemon=True)
                t.start()
                _workers.append(t)


def stop() -> None:
    _stop.set()


def depth() -> dict:
    client = _client()
    if client is None:
        return {"enabled": False}
    try:
        return {"enabled": enabled(), "waiting": client.llen(QUEUE_KEY),
                "running": client.llen(PROCESSING_KEY)}
    except Exception:  # noqa: BLE001
        return {"enabled": enabled(), "waiting": None, "running": None}
