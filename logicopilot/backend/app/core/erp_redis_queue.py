"""A durable queue for real ERP submissions, so a deploy stops losing the ones still waiting.

What was here before (and still is, as the fallback) is an in-process ThreadPoolExecutor:
`submit_erp_run` hands it a CLOSURE and it keeps the backlog in that process's memory. That
works until the process goes away - a pm2 restart, a deploy, a crash - and then every job
sitting in the backlog is gone with no trace. Nothing re-submits them; the operator sees a job
stuck on "AI - Preparing for ERP" forever and nobody can say why.

So the queue holds DATA, not a function: `{job_id, is_excel_entry}`, enough to rebuild the work
from scratch. Redis keeps it, workers pull from it, and a restart picks the backlog back up.

    enqueue()  ->  RPUSH  erp:queue
                              |
    worker     <-  BLMOVE  ---+---->  erp:processing   (reserved, not lost)
                                              |
                              run the job, then LREM it off

The middle list is what makes a killed worker survivable: the item stays in `erp:processing`
until the run finishes, and `requeue_orphans()` moves anything still sitting there back onto
the queue at startup. That is AT-LEAST-ONCE, which is only safe because `_run_gk2_submission`
already refuses to act on a job that has moved on (`gk2_status != "preparing_erp"`), so a
job that actually finished is a no-op the second time rather than a second ERP entry.

Redis is OPTIONAL. With no `redis_url` configured - or with Redis unreachable - every call
falls through to the in-process pool and the app behaves exactly as it did before. A queue
that cannot be reached must never stop a job running.
"""
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

QUEUE_KEY = "erp:queue"
PROCESSING_KEY = "erp:processing"

# How long a worker blocks on the queue before looping. Not a timeout on the work - it only
# decides how quickly a worker notices it has been told to stop.
_POP_TIMEOUT = 5

_client = None
_client_checked = False
_client_lock = threading.Lock()

_workers: list[threading.Thread] = []
_stop = threading.Event()


def _redis():
    """The shared client, or None when Redis is not configured or not reachable.

    Checked once: a Redis that is down at startup must not make every enqueue pay a
    connection timeout, and the fallback is good enough to run on indefinitely.
    """
    global _client, _client_checked
    with _client_lock:
        if _client_checked:
            return _client
        _client_checked = True
        try:
            from app.core.config import get_settings

            url = (getattr(get_settings(), "redis_url", "") or "").strip()
            if not url:
                logger.info("no redis_url configured - ERP jobs queue in-process, as before")
                return None
            import redis  # imported here so the package is optional

            client = redis.from_url(url, decode_responses=True,
                                    socket_connect_timeout=3, socket_timeout=5)
            client.ping()
            _client = client
            logger.info("ERP queue backed by Redis at %s", url.rsplit("@", 1)[-1])
        except Exception:  # noqa: BLE001 - any failure means "no Redis", never a broken app
            logger.warning("Redis is not reachable - ERP jobs queue in-process instead",
                           exc_info=True)
            _client = None
        return _client


def available() -> bool:
    return _redis() is not None


def queue_depth() -> dict:
    """What is waiting and what is in flight - for the Super Admin screen. Always answers,
    even with no Redis, so the caller never has to special-case it."""
    client = _redis()
    if client is None:
        return {"backend": "memory", "waiting": None, "running": None}
    try:
        return {"backend": "redis",
                "waiting": client.llen(QUEUE_KEY),
                "running": client.llen(PROCESSING_KEY)}
    except Exception:  # noqa: BLE001
        return {"backend": "redis", "waiting": None, "running": None}


def enqueue(job_id: str, *, is_excel_entry: bool, desired_workers: int) -> str:
    """Put one job on the queue. Returns "redis" or "memory" - which one actually took it.

    The in-process fallback runs the identical work through the identical function, so the
    only difference between the two is whether the backlog survives this process.
    """
    client = _redis()
    if client is not None:
        try:
            client.rpush(QUEUE_KEY, json.dumps(
                {"job_id": job_id, "is_excel_entry": bool(is_excel_entry),
                 "queued_at": time.time()}))
            _ensure_workers(desired_workers)
            return "redis"
        except Exception:  # noqa: BLE001 - fall through rather than lose the job
            logger.warning("could not queue job %s in Redis - running it in-process instead",
                           job_id, exc_info=True)

    from app.core.erp_queue import submit_erp_run

    submit_erp_run(desired_workers, lambda: run_one(job_id, bool(is_excel_entry)))
    return "memory"


def run_one(job_id: str, is_excel_entry: bool) -> None:
    """Do one job's real ERP submission. The single place both backends go through, so the
    two can never drift into doing different things."""
    # The badge would otherwise flick through "AI - Preparing for ERP" too fast to read. This
    # moves WHEN an already-decided outcome lands, never what it is - see the original call
    # site's own comment in jobs.py.
    time.sleep(2)
    from app.api.v1.jobs import _run_gk2_submission
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _run_gk2_submission(db, job_id, is_excel_entry)
    finally:
        db.close()


def _worker(name: str) -> None:
    client = _redis()
    if client is None:
        return
    logger.info("ERP queue worker %s started", name)
    while not _stop.is_set():
        raw = None
        try:
            # Reserve, do not just take: the item moves onto erp:processing and stays there
            # until the run is over, so a worker killed mid-job leaves something to find.
            raw = client.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=_POP_TIMEOUT)
        except Exception:  # noqa: BLE001 - a dropped connection must not kill the worker
            logger.warning("ERP queue worker %s could not read the queue", name, exc_info=True)
            _stop.wait(5)
            continue
        if raw is None:
            continue
        try:
            item = json.loads(raw)
            logger.info("ERP queue worker %s picked up job %s", name, item.get("job_id"))
            run_one(item["job_id"], bool(item.get("is_excel_entry")))
        except Exception:  # noqa: BLE001 - one bad job must never stop the worker
            logger.exception("ERP queue worker %s failed on %s", name, raw[:200])
        finally:
            # Off the in-flight list either way. A job that genuinely failed has its own
            # failure recorded on the job itself (see persist_run_outcome); leaving it here
            # would only make the next startup run it a second time for nothing.
            try:
                client.lrem(PROCESSING_KEY, 1, raw)
            except Exception:  # noqa: BLE001
                logger.warning("could not clear a finished job off %s", PROCESSING_KEY,
                               exc_info=True)
    logger.info("ERP queue worker %s stopped", name)


def _ensure_workers(desired: int) -> None:
    """Keep `desired` worker threads alive. Called on every enqueue, so a worker that died
    is replaced without anyone noticing, and a changed worker count takes effect by itself."""
    global _workers
    _workers = [t for t in _workers if t.is_alive()]
    for i in range(len(_workers), max(1, desired)):
        name = f"erp-q{i + 1}"
        t = threading.Thread(target=_worker, args=(name,), name=name, daemon=True)
        t.start()
        _workers.append(t)


def requeue_orphans() -> int:
    """Put back anything left reserved by a process that is no longer running.

    Called once at startup, which is the only moment it is safe: nothing of OURS is in flight
    yet, so everything sitting in erp:processing belongs to a run that did not survive.
    """
    client = _redis()
    if client is None:
        return 0
    moved = 0
    try:
        while True:
            raw = client.rpoplpush(PROCESSING_KEY, QUEUE_KEY)
            if raw is None:
                break
            moved += 1
            if moved > 1000:  # a runaway loop is worse than a few jobs left behind
                logger.error("stopped requeueing at 1000 - check %s by hand", PROCESSING_KEY)
                break
    except Exception:  # noqa: BLE001
        logger.warning("could not requeue interrupted ERP jobs", exc_info=True)
    if moved:
        logger.warning("requeued %d ERP job(s) interrupted by a restart", moved)
    return moved


def start(desired_workers: int) -> None:
    """Startup: pick the backlog back up and get the workers running."""
    if _redis() is None:
        return
    _stop.clear()
    requeue_orphans()
    _ensure_workers(desired_workers)


def stop() -> None:
    _stop.set()
