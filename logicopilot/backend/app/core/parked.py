"""Parked ERP sessions: a browser held open at a script's checkpoint, waiting for a job.

Only scripts whose Super Admin pressed "⚑ Capture checkpoint" AND ticked "Stay open" ever park.
Everything else behaves exactly as it always has: a job opens a browser, runs every step, closes.

The life of one parked session, which is a single `play_steps` run that pauses in the middle:

    launch ─ replay steps 1..checkpoint (log in, navigate) ─ PARK (block on the gate)
                                                              │
                          job arrives ────────────────────────┤
                                                              ▼
                              run the steps below the checkpoint ─ return status ─ close
                                                              │
                          then immediately ───────────────────┘
                              launch again, replay to the checkpoint, park again

So after every job — completed or failed — the browser closes, reopens, replays up to the
checkpoint and waits, which is the behaviour the Super Admin asked for.

Resilience: if the ERP is unreachable, the network drops, or the run crashes, the worker backs
off and parks again by itself. If the backend process restarts (pm2, deploy, reboot), every
eligible script is re-parked from `resume_all()` on startup.

Cost: a parked Chromium holds ~300 MB for as long as it is parked. `max_parked_sessions`
(default 1) is a hard cap, so this can never multiply into the whole machine.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# How long a session sits parked before it is torn down and rebuilt. An ERP will eventually
# log an idle session out; rebuilding on our own schedule means we discover that on a park,
# never in the middle of a customer's job.
PARK_REFRESH_SECONDS = 20 * 60

# Backoff after a failed park attempt: the ERP may be down or the network may be out.
BACKOFF_SECONDS = [15, 30, 60, 120, 300]

# How long a job will wait for a parked session to pick it up before the caller gives up and
# runs the script normally instead. A parked session should answer instantly; this only fires
# if the worker is wedged.
CLAIM_TIMEOUT = 20


class JobGate:
    """The rendezvous between a parked browser and an arriving job.

    `wait()` is called from inside the browser thread and blocks until a job is handed over
    (or the park is released). `deliver()` is called from the request thread and blocks until
    the browser finishes the entry, so the caller gets the run's result exactly as if it had
    called play_steps itself.
    """

    def __init__(self, on_park=None) -> None:
        self._on_park = on_park   # called the instant the browser actually starts waiting
        self._job: dict | None = None
        self._job_ready = threading.Event()
        self._result: dict | None = None
        self._result_ready = threading.Event()
        self._claimed = threading.Lock()

    # ---- browser side ----------------------------------------------------------------
    def wait(self) -> dict | None:
        """Block until a job arrives, the park is refreshed, or the session is stopped."""
        if self._on_park:
            self._on_park()   # the session is genuinely parked now, not merely starting
        if not self._job_ready.wait(timeout=PARK_REFRESH_SECONDS):
            # Idle refresh. Take the claim lock so a job arriving in this instant cannot hand
            # itself to a browser that has already stopped waiting - it falls back instead.
            self._claimed.acquire(blocking=False)
            return None
        return self._job

    def finish(self, result: dict) -> None:
        self._result = result
        self._result_ready.set()

    def release(self) -> None:
        """Wake a parked browser with no job — used to stop or refresh it."""
        self._job = None
        self._job_ready.set()

    # ---- job side --------------------------------------------------------------------
    def claim(self) -> bool:
        """Only one job may use a given parked session. Non-blocking."""
        return self._claimed.acquire(blocking=False)

    def deliver(self, values: dict, rows: dict | None, job_ref: str, timeout: int) -> dict | None:
        """Hand the job over and wait for the entry to finish. None means it never completed."""
        self._job = {"values": values, "rows": rows or {}, "job_ref": job_ref}
        self._job_ready.set()
        if not self._result_ready.wait(timeout=timeout):
            return None
        return self._result


class _Session:
    """One script's parked worker thread."""

    def __init__(self, script: dict) -> None:
        self.script = script          # plain dict snapshot: no ORM object crosses the thread
        self.script_id = script["id"]
        self.gate: JobGate | None = None
        self.phase = "starting"       # starting | parking | ready | busy | backoff | stopped
        self.since = time.time()
        self.detail = ""
        self.parks = 0
        self.jobs = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"park-{self.script_id[:8]}", daemon=True)

    # ---- status ----------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "script_id": self.script_id,
            "name": self.script.get("name"),
            "phase": self.phase,
            "waiting_for": round(time.time() - self.since, 1),
            "parks": self.parks,
            "jobs": self.jobs,
            "detail": self.detail,
        }

    def stop(self) -> None:
        self._stop.set()
        if self.gate:
            self.gate.release()

    # ---- worker ----------------------------------------------------------------------
    def _run(self) -> None:
        from app.core.browser import play_steps

        fails = 0
        while not self._stop.is_set():
            def _parked() -> None:
                self.parks += 1
                self.phase = "ready"
                self.detail = "parked at the checkpoint, waiting for a job"
                self.since = time.time()

            gate = JobGate(on_park=_parked)
            self.gate = gate
            self.phase = "parking"
            self.since = time.time()
            self.detail = "logging in and navigating to the entry screen"
            try:
                result = play_steps(
                    self.script["url"],
                    self.script.get("login"),
                    self.script.get("steps") or [],
                    {},                      # the job's values arrive through the gate
                    headless=True,
                    checkpoint_index=self.script.get("checkpoint_index"),
                    session_file=self.script.get("session_file"),
                    job_gate=gate,
                    time_budget=self.script.get("time_budget") or 180,
                    downloads_dir=self.script.get("downloads_dir"),
                )
            except Exception as exc:  # noqa: BLE001 — a parked worker must never die silently
                logger.exception("parked session for %s crashed", self.script_id)
                result = {"status": "error", "error": str(exc), "log": [], "reason": f"Parked session crashed: {exc}"}

            gate.finish(result)  # unblock any job waiting on this run

            status = result.get("status")
            if status == "parked_released":
                # Normal idle refresh or a deliberate stop: not a failure, no backoff.
                fails = 0
            elif gate._job is not None:
                # A real job went through, whatever its outcome. The session did its work.
                fails = 0
                self.jobs += 1
            else:
                # Never reached the checkpoint: ERP down, network out, selector moved.
                fails += 1
                wait = BACKOFF_SECONDS[min(fails - 1, len(BACKOFF_SECONDS) - 1)]
                self.phase = "backoff"
                self.detail = f"could not reach the checkpoint ({result.get('reason') or status}); retrying in {wait}s"
                logger.warning("park for %s failed (%s) - retry in %ss", self.script_id, status, wait)
                self.since = time.time()
                if self._stop.wait(timeout=wait):
                    break
        self.phase = "stopped"
        self.detail = ""


class _Manager:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self.cap = 1  # overwritten from settings on first use

    def _limit(self) -> int:
        try:
            from app.core.config import get_settings

            # 0 is a legitimate value: it turns parking off entirely.
            raw = getattr(get_settings(), "max_parked_sessions", 1)
            return max(0, int(raw if raw is not None else 1))
        except Exception:  # noqa: BLE001
            return 1

    def park(self, script: dict) -> dict:
        """Start (or keep) a parked session for this script. Returns a status snapshot."""
        with self._lock:
            live = {k: s for k, s in self._sessions.items() if s.thread.is_alive()}
            self._sessions = live
            existing = live.get(script["id"])
            if existing is not None:
                return existing.snapshot()
            cap = self._limit()
            if cap == 0:
                return {"script_id": script["id"], "phase": "disabled",
                        "detail": "parking is switched off (max_parked_sessions=0)"}
            if len(live) >= cap:
                return {"script_id": script["id"], "phase": "refused",
                        "detail": f"already {len(live)} parked session(s); the cap is {cap}"}
            sess = _Session(script)
            self._sessions[script["id"]] = sess
            sess.thread.start()
            logger.info("parked session started for script %s", script["id"])
            return sess.snapshot()

    def unpark(self, script_id: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(script_id, None)
        if sess is None:
            return False
        sess.stop()
        return True

    def unpark_all(self) -> int:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions = {}
        for s in sessions:
            s.stop()
        return len(sessions)

    def status(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in self._sessions.values()]

    def run_job(self, script_id: str, values: dict, rows: dict | None, job_ref: str,
                timeout: int = 240) -> dict | None:
        """Give a job to this script's parked session.

        Returns the run result, or None when there is no usable parked session — in which case
        the caller must run the script the normal way. Never raises: a parked session failing
        must degrade to the ordinary path, not fail the job.
        """
        with self._lock:
            sess = self._sessions.get(script_id)
        if sess is None or not sess.thread.is_alive() or sess.phase not in ("parking", "ready"):
            return None
        gate = sess.gate
        if gate is None or not gate.claim():
            return None
        sess.phase = "busy"
        sess.detail = f"running job {job_ref}"
        sess.since = time.time()
        try:
            res = gate.deliver(values, rows, job_ref, timeout=timeout)
            if res is None or res.get("status") == "parked_released":
                # The browser stopped waiting before we got there (idle refresh raced us).
                # Report no parked session so the caller runs the script the ordinary way.
                return None
            return res
        except Exception:  # noqa: BLE001
            logger.exception("handing job %s to the parked session failed", job_ref)
            return None


MANAGER = _Manager()


def resume_all(db_factory) -> list[dict]:
    """Re-park every eligible script. Called on backend startup, so a pm2 restart, a deploy or
    a reboot brings the parked sessions back by itself — the Super Admin never has to."""
    out: list[dict] = []
    try:
        from pathlib import Path

        from app.core.config import get_settings
        from app.models.erp_script import ErpScript

        db = db_factory()
        try:
            rows = (
                db.query(ErpScript)
                .filter(ErpScript.stay_open.is_(True), ErpScript.checkpoint_index.isnot(None))
                .all()
            )
            uploads = Path(get_settings().uploads_dir)
            for s in rows:
                out.append(MANAGER.park({
                    "id": s.id,
                    "name": s.name,
                    "url": s.url,
                    "steps": s.steps or [],
                    "checkpoint_index": s.checkpoint_index,
                    "login": ({"username": s.login_username or "", "password": s.login_password or ""}
                              if s.has_login else None),
                    "session_file": uploads / "erp_sessions" / f"{s.id}.json",
                }))
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — never stop the app from booting over this
        logger.exception("could not resume parked sessions")
    return out
