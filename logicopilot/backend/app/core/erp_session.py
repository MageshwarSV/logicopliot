"""One ERP login, shared by every job running at once.

Playwright already knows how to resume a logged-in session: `save_checkpoint_session` writes
the context's storage_state to a file at the checkpoint, and the next run hands it back to
`browser.new_context(storage_state=...)` and skips the login steps entirely. That part has
worked for a while. What it was never asked to survive is SEVERAL JOBS AT ONCE.

With erp_script_workers > 1, three jobs approved in the same minute all start together, all
find no saved session, and all replay the login. Three simultaneous logins into one ERP
account is the one thing this must not do - browser.py already carries the scar:

    "you are logged in from another portal, kill the other session?" got a silent No,
    and the login stopped there.

SOFTLINK offers to kill the OTHER session when a second login arrives. So a second job
logging in does not merely waste 40 seconds; it can log the first job out mid-entry, and the
first job finds its screen gone somewhere between filling a field and pressing Save. Many
tabs on one session is fine - that is the whole point of the design. Many LOGINS is not.

So logins are single-flighted. The first job to find no session takes an advisory lock and
logs in; everyone else waits for it to publish and then jumps straight to the entry screen:

    job A - no session - takes the lock - logs in - saves - publishes - runs its entry
    job B - no session - lock held ----- waits ------------' loads it - runs its entry
    job C - no session - lock held ----- waits ------------' loads it - runs its entry

The lock is `flock` on a sibling file, for the same reason worker_lock.py uses one: the
kernel drops it when the holder's process dies, so a leader that crashes or is killed mid-
login hands the job on to a waiter instead of wedging every later run behind a stale lockfile.
An in-process lock sits in front of it so threads in ONE process (the usual case - uvicorn
runs --workers 1 today) serialise without touching the filesystem at all.

Nothing here can fail a job. Every path ends in "log in yourself", because a job that logs in
redundantly is a far smaller problem than a job that does not run.
"""
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only - the servers are Linux
except ImportError:  # pragma: no cover - Windows: dev machine and the local test venv
    fcntl = None

logger = logging.getLogger(__name__)

# How long a job waits for whoever is logging in before giving up and logging in itself. Has
# to clear a real ERP login plus the navigation to the entry screen - SOFTLINK takes 20-40s
# on a good day. Generous on purpose: waiting costs one job some seconds, while a redundant
# login can cost ANOTHER job its session.
LOGIN_WAIT_SECONDS = 120

# How long a job waits for its turn on a script that CANNOT share a login. A real ERP entry
# runs in a minute or three, so this covers a couple of jobs queued ahead. On expiry the run
# goes ahead anyway: a job that never runs is worse than one that risks a second login.
SCRIPT_SLOT_WAIT_SECONDS = 300

_POLL_SECONDS = 0.5

# One lock per script, so two different scripts (different ERP accounts) never wait on
# each other. Keyed by the session file's path, which is already per-script.
_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


def _local_lock_for(key: str) -> threading.Lock:
    with _local_locks_guard:
        lk = _local_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _local_locks[key] = lk
        return lk


def _load(session_file):
    """The saved session, if there is a usable one. Late import: browser.py imports us."""
    from app.core.browser import load_checkpoint_session

    return load_checkpoint_session(session_file)


class SessionLease:
    """One job's claim on a script's shared ERP login.

    `state` set        - a live session was already saved; skip the login steps.
    `leader` True      - nobody had one; this job logs in and publishes it for the others.
    neither            - somebody else is logging in but did not finish in time, or there is
                         no session file at all; log in without the lock. Always allowed.
    """

    def __init__(self, session_file, state, url, *, leader: bool, local, handle) -> None:
        self.session_file = session_file
        self.state = state
        self.url = url
        self.leader = leader
        self._local = local      # threading.Lock held, or None
        self._handle = handle    # open file whose flock is held, or None

    # ---- leader side -------------------------------------------------------------------
    def published(self) -> None:
        """The login finished and the session is on disk. Wake the waiters immediately
        instead of leaving them on the poll - they are blocking real jobs."""
        self._unlock()

    def become_leader_for_replay(self) -> None:
        """A resumed run found its session dead and is about to replay the login.

        Same thundering herd as a cold start, one step later: several resumed jobs can all
        discover the session is gone in the same second. Take the lock before replaying so
        the others wait for this login rather than piling a third and fourth onto an ERP that
        has just decided it does not know us.
        """
        if self.leader or not self.session_file:
            return
        got = _take(_login_lock_path(self.session_file), wait_seconds=LOGIN_WAIT_SECONDS)
        if got is None:
            # Someone else is already replaying the login. Let this run do its own rather
            # than stall any longer - it has already spent its wait.
            logger.warning("could not take the login lock for a replay; logging in anyway")
            return
        self._local, self._handle = got
        self.leader = True

    # ---- resumed side ------------------------------------------------------------------
    def touch(self) -> None:
        """Mark the saved session as alive right now.

        A session is only reused while it is under CHECKPOINT_MAX_AGE (30 min), counted from
        the login that created it. Without this, a script fed a steady stream of jobs throws
        a perfectly good session away every half hour and logs in again - and that login is
        exactly the event that can kill the session a job is mid-entry on. The caller only
        calls this once the run has PROVED the session still works (the probe for the first
        element past the checkpoint passed), so this records evidence, never a guess.
        """
        if not self.session_file:
            return
        import json

        try:
            fp = Path(self.session_file)
            blob = json.loads(fp.read_text(encoding="utf-8"))
            blob["saved_at"] = time.time()
            atomic_write(fp, json.dumps(blob))
        except Exception:  # noqa: BLE001 - at worst the session ages out as it would have
            logger.debug("could not refresh the checkpoint session timestamp", exc_info=True)

    # ---- always ------------------------------------------------------------------------
    def release(self) -> None:
        """Called in the run's `finally`. A leader that got nowhere releases the lock here,
        so the next job logs in rather than waiting out the full timeout on a dead leader."""
        self._unlock()

    def _unlock(self) -> None:
        handle, self._handle = self._handle, None
        local, self._local = self._local, None
        self.leader = False
        if local is not None or handle is not None:
            _drop((local, handle))


def atomic_write(fp: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    `write_text` truncates first, so a reader landing in that window gets half a JSON
    document. One job could hand the other an unparseable session - survivable (it falls
    back to a login) but it is the exact redundant login this module exists to prevent.
    `os.replace` is atomic on both POSIX and Windows: a reader sees the old file or the new
    one, never a partial one.
    """
    import os

    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(fp.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, fp)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


def _login_lock_path(session_file) -> str:
    """Who is allowed to perform the login for this script."""
    return str(session_file) + ".login.lock"


def _run_lock_path(script_id) -> str:
    """Who is allowed to be RUNNING this script right now - only used by scripts that cannot
    share a login, where one run at a time is the only safe answer."""
    from app.core.config import get_settings

    return str(Path(get_settings().uploads_dir) / "erp_sessions" / f"{script_id}.run.lock")


def _take(lock_path: str, *, wait_seconds: float = 0.0):
    """Take a lock, optionally waiting for it. Returns (local_lock, flock_handle) or None.

    Both locks or neither: holding the in-process one while failing the file one would block
    every thread here behind a lock the holder never actually won.
    """
    deadline = time.time() + wait_seconds
    local = _local_lock_for(lock_path)
    while True:
        if local.acquire(blocking=False):
            handle = _flock(lock_path)
            if handle is not None or fcntl is None:
                return local, handle
            local.release()
        if time.time() >= deadline:
            return None
        time.sleep(_POLL_SECONDS)


def _drop(got) -> None:
    """Release what _take returned. Never raises - this runs in `finally` blocks."""
    local, handle = got
    if handle is not None:
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        except Exception:  # noqa: BLE001
            pass
    if local is not None:
        try:
            local.release()
        except Exception:  # noqa: BLE001
            pass


def _flock(lock_path: str):
    if fcntl is None:  # Windows: the in-process lock is the whole story here
        return None
    try:
        p = Path(lock_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        handle = open(p, "w")
    except Exception:  # noqa: BLE001 - an unwritable dir must not stop the run
        logger.debug("could not open the lock file %s", lock_path, exc_info=True)
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


@contextmanager
def script_slot(script_id, *, shared_login: bool):
    """Hold this script's run slot for the length of one ERP entry.

    This is what makes raising erp_script_workers safe rather than merely faster.

    A script WITH a checkpoint and "stay open" shares one login between every job running on
    it (the lease above), so any number may run at once - tabs on one session, which is the
    whole point. A script WITHOUT one has no session to share: every run replays the login,
    so two at once is two logins into the same ERP account, and SOFTLINK answers the second
    by offering to kill the first. Those runs take turns instead, exactly as they did when
    the worker count was 1.

    So turning the workers up speeds up the scripts that are set up to share a session and
    changes nothing for the ones that are not, instead of quietly making them unsafe.
    """
    if shared_login:
        yield True   # nothing to serialise: they are all on one login
        return
    got = _take(_run_lock_path(script_id), wait_seconds=SCRIPT_SLOT_WAIT_SECONDS)
    if got is None:
        logger.warning(
            "script %s has no shared session and another job has been running it for over "
            "%ss - going ahead anyway. If the ERP asks to kill the other session, that job "
            "may lose its screen. Give this script a checkpoint and 'stay open' to let its "
            "jobs share one login.", script_id, SCRIPT_SLOT_WAIT_SECONDS)
    try:
        yield got is not None
    finally:
        if got is not None:
            _drop(got)


def acquire(session_file) -> SessionLease:
    """Get this job onto the script's shared login, waiting for a login in flight.

    Never raises and never blocks past LOGIN_WAIT_SECONDS.
    """
    if not session_file:
        return SessionLease(None, None, None, leader=False, local=None, handle=None)

    key = _login_lock_path(session_file)
    state, url = _load(session_file)
    if state:
        return SessionLease(session_file, state, url, leader=False, local=None, handle=None)

    got = _take(key)
    if got is not None:
        logger.info("no ERP session saved - this job logs in and shares it (%s)", session_file)
        return SessionLease(session_file, None, None, leader=True, local=got[0], handle=got[1])

    # Another job is logging in. Wait for its session rather than starting a second login
    # that could log IT out.
    waited_from = time.time()
    deadline = waited_from + LOGIN_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(_POLL_SECONDS)
        state, url = _load(session_file)
        if state:
            logger.info("reusing the login another job just made (%.1fs wait)",
                        time.time() - waited_from)
            return SessionLease(session_file, state, url, leader=False, local=None, handle=None)
        got = _take(key)
        if got is not None:
            # The leader died or gave up without publishing - the kernel dropped its flock.
            logger.info("the job that was logging in did not finish - taking over")
            return SessionLease(session_file, None, None, leader=True, local=got[0], handle=got[1])

    logger.warning(
        "waited %ss for another job's ERP login and it never landed - logging in separately. "
        "If the ERP asks to kill the other session, that other job may lose its screen.",
        LOGIN_WAIT_SECONDS)
    return SessionLease(session_file, None, None, leader=False, local=None, handle=None)
