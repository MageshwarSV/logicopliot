"""The Entry Browser goes first; a queued ERP run it interrupts starts again from step 0 afterwards.

SOFTLINK allows one login per account. A Super Admin pressing Rerun (or step-by-step) in the
Entry Browser opens its OWN browser and logs in - which logs out whatever queued ERP run was
in the middle of an entry, leaving that job on "ERP Entry Process Started" with nothing running.

So a manual run takes a HOLD here, and while any hold is active:
  - a queued run already in the ERP stops at the end of the step it is on (play_steps' own
    `cancel` check, the same clean point the Stop button uses), goes back to "Preparing for
    ERP", waits, and when the hold ends runs again FROM THE START - not from where it stopped,
    because the half-filled ERP form it was on is gone with its logout;
  - a queued run that has not started yet waits before touching the ERP at all.

The manual run itself waits (bounded) for the interrupted runs to close their browsers before it
logs in, so the two never overlap.

A hold ends when the manual run finishes, or when its browser is gone (the session reaper closes
an abandoned step-by-step browser after its idle limit), so a forgotten Entry Browser tab cannot
freeze the queue for ever. HOLD_MAX_SECONDS is a last-resort ceiling on top of that.

In-process only: the backend runs one uvicorn worker, and every ERP run (queue workers, Entry
Browser threads) lives in that process.
"""
import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable

logger = logging.getLogger(__name__)

HOLD_MAX_SECONDS = 2 * 60 * 60

_lock = threading.Lock()
# key -> (alive() callable, hard deadline on the monotonic clock)
_holds: dict[str, tuple[Callable[[], bool], float]] = {}
# job ids of queued runs currently inside play_steps
_running: set[str] = set()


def _purge() -> None:
    now = time.monotonic()
    for key, (alive, deadline) in list(_holds.items()):
        try:
            ok = now < deadline and bool(alive())
        except Exception:  # noqa: BLE001 - a broken liveness check must not wedge the queue
            ok = False
        if not ok:
            _holds.pop(key, None)
            logger.info("ERP priority: hold %s ended", key)


def manual_active() -> bool:
    """True while an Entry Browser run holds the ERP. Used as play_steps' `cancel` for queued runs."""
    with _lock:
        _purge()
        return bool(_holds)


def begin_manual(key: str, alive: Callable[[], bool] | None = None) -> None:
    with _lock:
        _holds[key] = (alive or (lambda: True), time.monotonic() + HOLD_MAX_SECONDS)
        running = sorted(_running)
    logger.info("ERP priority: Entry Browser run %s takes the ERP%s", key,
                f" - pausing queued run(s) {', '.join(running)}" if running else "")


def set_alive(key: str, alive: Callable[[], bool]) -> None:
    with _lock:
        if key in _holds:
            _holds[key] = (alive, _holds[key][1])


def end_manual(key: str) -> None:
    with _lock:
        _holds.pop(key, None)
    logger.info("ERP priority: Entry Browser run %s released the ERP", key)


def wait_for_queue_to_pause(timeout: float) -> bool:
    """Called by the manual run before it logs in: wait until every queued run has closed its
    browser. True if they all did within `timeout`."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _running:
                return True
        time.sleep(1)
    with _lock:
        left = sorted(_running)
    logger.warning("ERP priority: queued run(s) %s still closing after %ss - starting anyway",
                   ", ".join(left), timeout)
    return False


def wait_turn() -> None:
    """Called by a queued run before it touches the ERP: wait out any Entry Browser run."""
    logged = False
    while manual_active():
        if not logged:
            logger.info("ERP priority: queued run waiting for the Entry Browser to finish")
            logged = True
        time.sleep(2)


@contextmanager
def queued_run(job_id: str):
    """Mark a queued run as inside the ERP for the length of the block."""
    with _lock:
        _running.add(job_id)
    try:
        yield
    finally:
        with _lock:
            _running.discard(job_id)


def was_preempted(result: dict | None) -> bool:
    """A queued run that play_steps stopped through `cancel`. Queued runs have no Stop button of
    their own, so `manual_active` is the only thing that can have stopped one."""
    return bool(result and result.get("stopped"))
