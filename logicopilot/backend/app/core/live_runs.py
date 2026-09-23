"""The latest frame of a job's ERP run, so it can be watched while it happens.

`play_steps` already offers a `progress(screenshot, log)` hook - the Entry Browser has used it
all along - but a job run never passed one, so a run was invisible from the moment it started
until the moment it ended. The only way to see what an entry was doing was to wait for it to
fail and read the screenshot it left behind.

Kept in memory on purpose. A frame is a PNG of a full ERP screen several hundred KB at a time,
several times a step; writing that to the database would put megabytes per run into a disk
already at 91%, to store something nobody looks at once the run is over.
"""
import threading
import time

# job_id -> {"shot": base64 png, "log": [str], "at": epoch seconds, "done": bool}
_frames: dict[str, dict] = {}
_lock = threading.Lock()

# A finished run's last frame is kept this long so the screen does not go blank the instant the
# entry lands - whoever is watching gets to see where it ended up.
KEEP_AFTER_DONE = 300.0

# Nothing removes a job from the map on its own (a crashed run never reports "done"), so a frame
# older than this is dropped on the next write. Long enough to outlast any real run - one is
# capped at MAX_RUN_SECONDS - and short enough that the map cannot grow all day.
STALE_AFTER = 1800.0


def publish(job_id: str, shot: str | None, log: list[str], done: bool = False,
            debug: dict | None = None) -> None:
    """Record where a run has got to. Called from the run's own thread, so it must not raise."""
    if not job_id:
        return
    now = time.time()
    with _lock:
        prev = _frames.get(job_id) or {}
        _frames[job_id] = {
            # A step that could not be photographed keeps the previous picture rather than
            # blanking the screen - the log line still tells the watcher what happened.
            "shot": shot or prev.get("shot"),
            "log": list(log or prev.get("log") or []),
            "at": now,
            "done": done,
            # What the run is waiting for at this moment: the step, the page's state, and
            # whether the element is missing / invisible / covered. A screenshot cannot tell
            # those four apart, and each needs a different fix.
            "debug": debug if debug is not None else prev.get("debug"),
        }
        for jid, f in list(_frames.items()):
            if now - f["at"] > STALE_AFTER:
                del _frames[jid]


def latest(job_id: str) -> dict | None:
    """The most recent frame, or None once there is nothing worth showing."""
    with _lock:
        f = _frames.get(job_id)
        if f is None:
            return None
        if f["done"] and time.time() - f["at"] > KEEP_AFTER_DONE:
            del _frames[job_id]
            return None
        return dict(f)


def clear(job_id: str) -> None:
    with _lock:
        _frames.pop(job_id, None)


# A run photographs the page at every step, so frames arrive constantly while one is alive.
# Nothing for this long means the run is not running - its thread died, almost always because
# the server was restarted under it.
SILENT_IS_STRANDED = 180.0


def is_alive(job_id: str) -> bool:
    """Is a run genuinely still going, or is the job merely STUCK on `processing`?

    The status column cannot answer this. It is set to `processing` before the work starts and
    only rewritten when the work finishes, so a run whose thread is gone leaves the job saying
    `processing` for ever - and nothing in the app ever moved it back.
    """
    f = latest(job_id)
    if f is None or f.get("done"):
        return False
    return (time.time() - f["at"]) < SILENT_IS_STRANDED
