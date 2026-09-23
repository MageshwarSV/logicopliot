"""Picks exactly one process to own the app's singleton background work.

Uvicorn's own --workers flag forks several independent OS processes, each running its
own copy of this app and firing every @app.on_event("startup") handler. That is fine for
handling HTTP requests (the whole point of adding workers), but wrong for the handlers
that start an ongoing background job: the email poller and the parked-browser-session
resume. Four workers each polling the same mailboxes, or each re-driving the same ERP
browser session, is not four times the throughput - it is the same mailbox read (and the
same ERP entry submitted) multiple times over.

A plain, non-blocking flock on a fixed file is the standard fix: every worker tries to
take it at startup, exactly one succeeds and keeps the file open (and therefore the lock
held) for its whole lifetime, and the rest skip. Whichever worker pm2/uvicorn happens to
start first wins - which one does not matter, only that it is one.
"""
import logging
from pathlib import Path

try:
    import fcntl  # POSIX only - production runs on Linux
except ImportError:  # pragma: no cover - Windows: dev machine and the local test venv
    fcntl = None

logger = logging.getLogger(__name__)

_LOCK_PATH = Path(__file__).resolve().parent.parent.parent / "singleton_worker.lock"
_held_handle = None  # kept open for the process lifetime - closing it releases the lock


def am_i_the_singleton_worker() -> bool:
    """True in exactly one of this app's worker processes, for as long as it runs.

    Always True where there is no `fcntl` (Windows) - only Linux production ever runs
    uvicorn with --workers, so there is nothing to arbitrate between there.
    """
    global _held_handle
    if fcntl is None:
        return True
    if _held_handle is not None:
        return True
    handle = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        logger.info("another worker process already owns the singleton background work")
        return False
    _held_handle = handle  # never closed on purpose - held until this process exits
    logger.info("this worker process owns the singleton background work (pid lock)")
    return True
