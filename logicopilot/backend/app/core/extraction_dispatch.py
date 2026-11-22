"""Run / Re-run extraction from the website, on a split setup: the WEB server does not read the
job itself, it hands the job to the WORKER server through Redis (Step 1.2 of the 4-server plan).

With PROCESS_ROLE "all" (every server today) none of this is used: the extraction starts in
this process exactly as before.

The database stays the source of truth. By the time a job is handed off it is already
`extracting` and committed (_begin_extraction), so Redis is only the doorbell:
  - a message lost with Redis, or with a worker that died mid-read, is not a lost job - the
    worker's startup resume (restart_resume) finds every job still `extracting` and reads it;
  - the worker therefore empties the doorbell before that resume, and skips any message for a
    job that is no longer `extracting` or is already being read in this process.

FIFO: lpush here, brpop there.
"""
from __future__ import annotations

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

QUEUE_KEY = "xq:extract"
_POP_TIMEOUT = 5          # below erp_redis_queue's socket timeout (pop + 10)

_inflight: set[str] = set()
_inflight_lock = threading.Lock()
_started = False
_start_lock = threading.Lock()


def _client():
    from app.core.erp_redis_queue import _redis

    return _redis()


def hand_off(job_id: str, only_job_document_id: str | None, is_rerun: bool) -> bool:
    """True when the extraction was handed to the worker server; False = run it here.

    Here = any process that runs extraction itself ("all" or "worker"). A web/erp process with
    no Redis also runs it here, loudly, rather than leave the job saying AI Processing forever.
    """
    from app.core.process_role import runs

    if runs("extraction"):
        return False
    client = _client()
    if client is None:
        logger.error("job %s: extraction should go to the worker server but Redis is not "
                     "available - running it in this process instead", job_id)
        return False
    try:
        client.lpush(QUEUE_KEY, json.dumps({"job_id": job_id, "doc": only_job_document_id,
                                            "is_rerun": bool(is_rerun), "at": time.time()}))
    except Exception:  # noqa: BLE001
        logger.exception("job %s: could not queue the extraction for the worker - running "
                         "it in this process instead", job_id)
        return False
    logger.info("job %s: extraction handed to the worker server", job_id)
    return True


def claim(job_id: str) -> bool:
    """Mark a job as being read in this process. False = it already is (a doorbell message and
    the startup resume can both name the same job; only one may read it)."""
    with _inflight_lock:
        if job_id in _inflight:
            return False
        _inflight.add(job_id)
        return True


def release(job_id: str) -> None:
    with _inflight_lock:
        _inflight.discard(job_id)


def clear_queue() -> None:
    """Worker startup, BEFORE the resume: everything waiting is `extracting` in the database,
    so the resume reads it - the doorbell messages for it would only be duplicates."""
    client = _client()
    if client is None:
        return
    try:
        n = client.llen(QUEUE_KEY)
        client.delete(QUEUE_KEY)
        if n:
            logger.info("extraction doorbell: %d waiting message(s) left to the startup resume", n)
    except Exception:  # noqa: BLE001
        logger.exception("could not clear the extraction doorbell")


def _handle(raw: str) -> None:
    from app.api.v1.jobs import _start_extraction_background
    from app.db.session import SessionLocal
    from app.models.job import Job

    try:
        msg = json.loads(raw)
        job_id = msg["job_id"]
    except Exception:  # noqa: BLE001
        logger.warning("extraction doorbell: unreadable message %r - skipped", raw[:200])
        return
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        status = job.status if job is not None else None
    finally:
        db.close()
    if status != "extracting":
        logger.info("extraction doorbell: job %s is %s now - nothing to read", job_id, status)
        return
    _start_extraction_background(job_id, only_job_document_id=msg.get("doc") or None,
                                 is_rerun=bool(msg.get("is_rerun")))


def _loop() -> None:
    while True:
        client = _client()
        if client is None:
            time.sleep(30)
            continue
        try:
            item = client.brpop(QUEUE_KEY, timeout=_POP_TIMEOUT)
        except Exception:  # noqa: BLE001
            logger.exception("extraction doorbell: Redis read failed - retrying")
            time.sleep(5)
            continue
        if not item:
            continue
        try:
            _handle(item[1])
        except Exception:  # noqa: BLE001
            logger.exception("extraction doorbell: could not start %r", item[1][:200])


def start() -> None:
    """Worker server only (PROCESS_ROLE=worker). Idempotent."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="extraction-doorbell").start()
    logger.info("extraction doorbell started - website Run / Re-run extraction is read here")
