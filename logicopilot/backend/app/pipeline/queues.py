"""Durable stage queues in Redis, and the counters that move a job from one stage to the next.

One queue per stage. An item holds DATA (job id, page or document number), never a function, so
any process can rebuild the work from it. The pattern is Ashraf's ERP queue
(app/core/erp_redis_queue.py), with two differences:

  - Oldest first. Items are added with LPUSH and taken from the other end (BRPOPLPUSH), so the
    queue is first-in, first-out. (RPUSH + BRPOPLPUSH, as the ERP queue does, takes the newest.)
  - Retries. A failed item goes back on its queue up to MAX_ATTEMPTS times, then onto the stage's
    dead list with the error, where it can be seen instead of looping for ever.

    enqueue  ->  LPUSH       p2:q:{stage}
    reserve  <-  BRPOPLPUSH  p2:q:{stage}  ->  p2:p:{stage}   (reserved, not lost)
    ack          LREM        p2:p:{stage}
    restart      everything left in p2:p:{stage} goes back to p2:q:{stage} (requeue_orphans)

Fan-in: OCR runs one item per page, but classification must start once, after the LAST page.
mark_done() records each finished page in a set (so a page done twice - after a restart - counts
once) and returns (done, total, first_time). The caller moves the job on only when done == total
AND first_time, which is true for exactly one call.

Redis is shared with the ERP queue (same REDIS_URL, same client); every key here starts "p2:".
"""
from __future__ import annotations

import json
import logging
import time
import uuid

logger = logging.getLogger(__name__)

PREFIX = "p2"
MAX_ATTEMPTS = 3
STAGES = (
    "ingest", "ocr", "classify", "extract", "finalize",
    "large-ingest", "large-ocr", "large-classify", "large-extract",
    "ping",
)


def _redis():
    from app.core.erp_redis_queue import _redis as shared_client

    return shared_client()


def available() -> bool:
    return _redis() is not None


def _check_stage(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"unknown pipeline stage: {stage!r}")
    return stage


def queue_key(stage: str) -> str:
    return f"{PREFIX}:q:{_check_stage(stage)}"


def processing_key(stage: str) -> str:
    return f"{PREFIX}:p:{_check_stage(stage)}"


def dead_key(stage: str) -> str:
    return f"{PREFIX}:dead:{_check_stage(stage)}"


def enqueue(stage: str, payload: dict) -> bool:
    """Add one item. False when there is no Redis (the caller keeps the old path)."""
    client = _redis()
    if client is None:
        return False
    item = {"id": uuid.uuid4().hex, "attempts": 0, "queued_at": time.time(), **payload}
    client.lpush(queue_key(stage), json.dumps(item, default=str))
    return True


def reserve(stage: str, timeout: int = 5) -> tuple[str, dict] | None:
    """The oldest waiting item, moved onto the reserved list until ack() or retry()."""
    client = _redis()
    if client is None:
        return None
    raw = client.brpoplpush(queue_key(stage), processing_key(stage), timeout=timeout)
    if raw is None:
        return None
    try:
        return raw, json.loads(raw)
    except ValueError:
        logger.error("pipeline %s: unreadable item dropped: %s", stage, str(raw)[:200])
        client.lrem(processing_key(stage), 1, raw)
        return None


def ack(stage: str, raw: str) -> None:
    client = _redis()
    if client is not None:
        client.lrem(processing_key(stage), 1, raw)


def retry(stage: str, raw: str, item: dict, error: str) -> str:
    """Hand a failed item back ("retry") or, after MAX_ATTEMPTS, to the dead list ("dead")."""
    client = _redis()
    if client is None:
        return "lost"
    client.lrem(processing_key(stage), 1, raw)
    item = {**item, "attempts": int(item.get("attempts") or 0) + 1, "last_error": error[:500]}
    if item["attempts"] >= MAX_ATTEMPTS:
        client.lpush(dead_key(stage), json.dumps(item, default=str))
        logger.error("pipeline %s: item %s gave up after %d attempts: %s",
                     stage, item.get("id"), item["attempts"], error[:200])
        return "dead"
    client.lpush(queue_key(stage), json.dumps(item, default=str))
    return "retry"


def requeue_orphans(stage: str) -> int:
    """At startup: everything still reserved belongs to a process that no longer runs."""
    client = _redis()
    if client is None:
        return 0
    moved = 0
    while moved < 10000:  # a runaway loop is worse than a few items left behind
        if client.rpoplpush(processing_key(stage), queue_key(stage)) is None:
            break
        moved += 1
    if moved:
        logger.warning("pipeline %s: %d item(s) interrupted by a restart put back", stage, moved)
    return moved


def depth(stage: str) -> dict:
    client = _redis()
    if client is None:
        return {"waiting": None, "running": None, "dead": None}
    return {"waiting": client.llen(queue_key(stage)), "running": client.llen(processing_key(stage)),
            "dead": client.llen(dead_key(stage))}


# ---- fan-in counters ---------------------------------------------------------------------

def _counter_key(job_id: str) -> str:
    return f"{PREFIX}:c:{job_id}"


def _done_key(job_id: str, stage: str) -> str:
    return f"{PREFIX}:d:{job_id}:{_check_stage(stage)}"


def set_total(job_id: str, stage: str, total: int) -> None:
    client = _redis()
    if client is not None:
        client.hset(_counter_key(job_id), f"{_check_stage(stage)}:total", int(total))


def mark_done(job_id: str, stage: str, unit: str) -> tuple[int, int, bool]:
    """(done, total, first_time). `unit` is the page or document number - doing the same unit
    twice (a retry, a restart) adds nothing, so done never runs past total."""
    client = _redis()
    if client is None:
        return 0, 0, False
    first_time = bool(client.sadd(_done_key(job_id, stage), str(unit)))
    done = int(client.scard(_done_key(job_id, stage)))
    total = int(client.hget(_counter_key(job_id), f"{stage}:total") or 0)
    return done, total, first_time


def progress(job_id: str) -> dict:
    """{"ocr": {"done": 7, "total": 10}, ...} for the stages this job has counters for."""
    client = _redis()
    if client is None:
        return {}
    out: dict = {}
    for field, total in (client.hgetall(_counter_key(job_id)) or {}).items():
        stage = field.rsplit(":", 1)[0]
        out[stage] = {"done": int(client.scard(_done_key(job_id, stage))), "total": int(total)}
    return out


def clear(job_id: str) -> None:
    client = _redis()
    if client is None:
        return
    keys = [_counter_key(job_id)] + [_done_key(job_id, s) for s in STAGES]
    client.delete(*keys)
