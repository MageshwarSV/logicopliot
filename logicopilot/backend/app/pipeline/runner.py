"""Worker threads that drain the pipeline's stage queues.

Each stage with a registered handler gets `threads_per_stage` threads. A thread reserves the
oldest item, runs the handler, and acks it; if the handler raises, the item is retried
(queues.retry) up to queues.MAX_ATTEMPTS and then parked on the stage's dead list. One bad item
never stops a thread.

Where it runs: for now inside the backend process (the singleton one that also runs the mail
cycle and the ERP queue), started from main.py when settings.pipeline_v2 is on and Redis is
reachable. The same start() is what a separate logicopilot-worker process will call later - only
the entry point changes, not this code.

Handlers are registered by the pieces that own a stage (OCR, classification, extraction, the
finalizer). This piece registers only "ping", a do-nothing stage used to prove on SIT that items
flow, survive a restart, and are counted - before any real work is routed here.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app.pipeline import queues

logger = logging.getLogger(__name__)

Handler = Callable[[dict], None]

_handlers: dict[str, Handler] = {}
_threads: list[threading.Thread] = []
_stop = threading.Event()
_lock = threading.Lock()
_started = False


def register(stage: str, handler: Handler) -> None:
    queues._check_stage(stage)
    _handlers[stage] = handler


def handlers() -> dict[str, Handler]:
    return dict(_handlers)


def enabled() -> bool:
    """On only where the setting says so AND Redis answers - otherwise nothing starts."""
    from app.core.config import get_settings

    return bool(get_settings().pipeline_v2) and queues.available()


def run_one(stage: str, timeout: int = 5) -> str | None:
    """Take and process one item: "done", "retry", "dead", or None when the queue was empty.
    The thread loop is just this, repeated - tests call it directly."""
    got = queues.reserve(stage, timeout=timeout)
    if got is None:
        return None
    raw, item = got
    handler = _handlers.get(stage)
    if handler is None:
        return queues.retry(stage, raw, item, "no handler registered for this stage")
    try:
        handler(item)
    except Exception as exc:  # noqa: BLE001 - one bad item must never stop the worker
        logger.exception("pipeline %s: item %s failed", stage, item.get("id"))
        return queues.retry(stage, raw, item, f"{type(exc).__name__}: {exc}")
    queues.ack(stage, raw)
    return "done"


def _loop(stage: str, name: str) -> None:
    logger.info("pipeline worker %s started", name)
    while not _stop.is_set():
        try:
            run_one(stage)
        except Exception:  # noqa: BLE001 - Redis hiccup: wait, then carry on
            logger.warning("pipeline worker %s could not read its queue", name, exc_info=True)
            _stop.wait(5)
    logger.info("pipeline worker %s stopped", name)


def start(threads_per_stage: int = 2) -> list[str]:
    """Hand back items left reserved by the previous process, then start the threads. Idempotent.
    Returns the stages started."""
    global _started
    with _lock:
        if _started:
            return []
        _started = True
    _stop.clear()
    started = []
    for stage in sorted(_handlers):
        queues.requeue_orphans(stage)
        for i in range(max(1, threads_per_stage)):
            name = f"p2-{stage}-{i + 1}"
            t = threading.Thread(target=_loop, args=(stage, name), name=name, daemon=True)
            t.start()
            _threads.append(t)
        started.append(stage)
    logger.info("pipeline started: %s", ", ".join(started) or "no stages")
    return started


def stop() -> None:
    global _started
    _stop.set()
    with _lock:
        _started = False


# ---- the "ping" stage: proves the plumbing on SIT, does no real work ---------------------

PING_LAST_KEY = f"{queues.PREFIX}:ping:last"


def _ping(item: dict) -> None:
    """Records that it ran, and counts itself as one unit of the batch it belongs to."""
    if item.get("fail"):
        raise RuntimeError("ping asked to fail (testing retries)")
    seconds = float(item.get("sleep") or 0)
    if seconds:
        time.sleep(min(seconds, 30))
    client = queues._redis()
    if client is not None:
        client.set(PING_LAST_KEY, f"{item.get('id')} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if item.get("batch"):
        queues.mark_done(item["batch"], "ping", item.get("unit", item.get("id")))


register("ping", _ping)


def send_pings(count: int, sleep: float = 0.0, fail: bool = False) -> str | None:
    """Queue `count` ping items as one batch; returns the batch id (None without Redis)."""
    import uuid

    if not queues.available():
        return None
    batch = f"ping-{uuid.uuid4().hex[:10]}"
    queues.set_total(batch, "ping", count)
    for n in range(count):
        queues.enqueue("ping", {"batch": batch, "unit": n, "sleep": sleep, "fail": fail})
    return batch


def status() -> dict:
    """For Platform Settings: on/off, Redis, each stage's queue, the last ping."""
    client = queues._redis()
    last = None
    if client is not None:
        try:
            last = client.get(PING_LAST_KEY)
        except Exception:  # noqa: BLE001
            last = None
    return {
        "enabled": enabled(),
        "redis": client is not None,
        "running": _started,
        "stages": {s: queues.depth(s) for s in queues.STAGES},
        "handlers": sorted(_handlers),
        "last_ping": last,
    }
