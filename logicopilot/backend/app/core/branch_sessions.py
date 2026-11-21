"""Always-on ERP logins, one per branch, and the jobs that run on them in parallel.

Why this exists - measured on the live Live Impex ERP (20 Nov 2026):

  * a second login with the same ERP user logs the first one out;
  * tabs opened from ONE login all stay logged in, so many jobs can share a login;
  * but the branch belongs to the login, not the tab: switch one tab to Bangalore and every
    other tab on that login is entering into Bangalore too, whatever its header still says.

So jobs for different branches can only run side by side on different ERP users. Each
ErpBranchSession row is one of them. This module:

  1. KEEPS each enabled branch logged in (one keeper thread per row, in the singleton worker):
     replays the row's login script up to its checkpoint with the branch's own user and
     branch, saves the logged-in browser state to uploads/erp_sessions/branch_<id>.json, then
     re-checks it every CHECK_SECONDS - reload, look for the entry screen, save again - and
     logs in afresh the moment it has dropped or a job reports it gone.

  2. RUNS jobs on it: a job whose script has a `branch_field` and whose branch has a live row
     opens its OWN browser with that saved state and runs its stream script from after the
     checkpoint - no login at all. Up to `max_tabs` jobs per branch at once (a file lock per
     slot, so it holds across uvicorn processes). A job that finds the login dead hands it
     back to the keeper, waits for it to come back, and goes again.

Nothing here logs in from a job. The keeper is the only thing that ever logs a branch's user
in, which is what keeps one job from logging out every other job on its branch.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CHECK_SECONDS = 300          # health check (reload + look for the entry screen) per branch
SUPERVISE_SECONDS = 10       # how often the supervisor syncs keepers with the table
RELOGIN_BACKOFF = (60, 120, 300)  # wait after a failed login: 1, 2, then every 5 minutes
LIVE_WAIT_SECONDS = 300      # how long a job waits for its branch's login to come (back)
SLOT_WAIT_SECONDS = 3600     # how long a job waits for a free tab on its branch

_keepers: dict[str, "_Keeper"] = {}
_keepers_guard = threading.Lock()
_supervisor: threading.Thread | None = None
_stop = threading.Event()


# ---------------------------------------------------------------------------------------------
# files shared between the keeper and the jobs (any process)
# ---------------------------------------------------------------------------------------------

def _dir() -> Path:
    from app.core.config import get_settings

    d = Path(get_settings().uploads_dir) / "erp_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_file(bs_id: str) -> Path:
    return _dir() / f"branch_{bs_id}.json"


def _relogin_flag(bs_id: str) -> Path:
    return _dir() / f"branch_{bs_id}.relogin"


def live_state(bs_id: str) -> tuple[dict | None, str | None]:
    """The branch's saved logged-in state and the URL it stood on, or (None, None).

    load_checkpoint_session ignores a file older than 30 minutes; the keeper re-saves it on
    every health check, so an old file means the keeper itself is gone - not usable."""
    from app.core.browser import load_checkpoint_session

    return load_checkpoint_session(session_file(bs_id))


def report_lost(bs_id: str) -> None:
    """A job found the login dead: stop handing it out, and have the keeper log in again."""
    from app.core.browser import discard_checkpoint_session

    discard_checkpoint_session(session_file(bs_id))
    try:
        _relogin_flag(bs_id).touch()
    except Exception:  # noqa: BLE001
        logger.exception("could not flag branch session %s for a new login", bs_id)


def request_relogin(bs_id: str) -> None:
    """Super Admin pressed "Log in again"."""
    report_lost(bs_id)


def wait_live(bs_id: str, timeout: float = LIVE_WAIT_SECONDS) -> tuple[dict | None, str | None]:
    deadline = time.monotonic() + timeout
    while True:
        state, url = live_state(bs_id)
        if state is not None or time.monotonic() >= deadline:
            return state, url
        time.sleep(3)


# A priority waiter (an Entry Browser run somebody pressed) keeps a marker file fresh while it
# waits; queued jobs leave freed slots alone while one exists. Stale after this many seconds,
# so a waiter that died never blocks the queue.
_PRIORITY_FRESH_SECONDS = 6


def _priority_waiting(bs_id: str) -> bool:
    now = time.time()
    for f in _dir().glob(f"branch_{bs_id}.prio.*"):
        try:
            if now - f.stat().st_mtime < _PRIORITY_FRESH_SECONDS:
                return True
            f.unlink(missing_ok=True)
        except OSError:
            continue
    return False


@contextmanager
def tab_slot(bs_id: str, max_tabs: int, wait_seconds: float = SLOT_WAIT_SECONDS, *,
             priority: bool = False, on_wait=None):
    """Hold one of the branch's `max_tabs` run slots for the length of one entry.

    priority: this run goes first - the moment any slot frees it takes it, ahead of queued
    jobs (which hold back while a priority run is waiting). on_wait(message) is called when the
    branch turns out to be full, and on_wait(None) once a slot is taken."""
    import uuid as _uuid

    from app.core.erp_session import _drop, _take

    n = max(1, int(max_tabs or 1))
    deadline = time.monotonic() + wait_seconds
    marker = _dir() / f"branch_{bs_id}.prio.{_uuid.uuid4().hex[:10]}" if priority else None
    told = False
    got = None
    try:
        while got is None:
            if priority or not _priority_waiting(bs_id):
                for k in range(n):
                    got = _take(str(_dir() / f"branch_{bs_id}.tab{k}.lock"), wait_seconds=0)
                    if got is not None:
                        break
            if got is not None:
                break
            if marker is not None:
                marker.touch()
            if on_wait is not None and not told:
                told = True
                try:
                    on_wait(n)
                except Exception:  # noqa: BLE001
                    pass
            if time.monotonic() >= deadline:
                break
            time.sleep(1)
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)
    if got is None:
        logger.warning("branch session %s: no free tab after %ss - running anyway", bs_id, wait_seconds)
    if told and on_wait is not None:
        try:
            on_wait(None)
        except Exception:  # noqa: BLE001
            pass
    try:
        yield
    finally:
        if got is not None:
            _drop(got)


# ---------------------------------------------------------------------------------------------
# job side
# ---------------------------------------------------------------------------------------------

def branch_for_job(db, script, values: dict) -> dict | None:
    """The enabled branch session this job should run on, as a plain dict, or None.

    None = run the script's own way, exactly as before: the script has no branch field.
    {"missing": reason} = the script IS branch-routed but this job cannot be: no value in the
    field, or no enabled branch login for it. That job must fail with the reason - falling back
    to the script's own login could log a branch's user out from under every job on it."""
    field = (getattr(script, "branch_field", None) or "").strip()
    if not field:
        return None
    raw = str((values or {}).get(field) or "").strip()
    want = raw.casefold()
    if not want:
        return {"missing": f"This job has no {field}, so there is no branch login to enter it on. "
                           f"Fill in {field}, then press Retry."}
    from app.models.erp_branch_session import ErpBranchSession

    same_branch = [bs for bs in db.query(ErpBranchSession).filter(
        ErpBranchSession.tenant_id == script.tenant_id, ErpBranchSession.enabled.is_(True)).all()
        if (bs.branch or "").strip().casefold() == want]
    # A login ticked for this script beats one left on "all scripts".
    for bs in sorted(same_branch, key=lambda b: 0 if b.script_ids else 1):
        if not bs.script_ids or script.id in bs.script_ids:
            return {"id": bs.id, "branch": bs.branch, "max_tabs": bs.max_tabs}
    if same_branch:
        return {"missing": f"The {raw} branch login is not ticked for the {script.name} script. "
                           "Tick it under ERP Scripts → Branch logins → Used by scripts, then press Retry."}
    return {"missing": f"No ERP login is set up for the {raw} branch. Add one under ERP Scripts "
                       f"→ Branch logins (or correct the job's {field}), then press Retry."}


def play_on_branch(bs: dict, run, *, priority: bool = False, on_wait=None) -> dict:
    """Run one job on its branch's live login. `run(branch_session)` is the play_steps call.

    Takes a tab slot, waits for the login to be live, runs. A run that finds the login dead
    (session_lost) reports it, waits for the keeper's new login and runs again - twice at most,
    so an ERP that keeps refusing us ends as a failed job with a reason, not a loop."""
    res: dict = {}
    with tab_slot(bs["id"], bs.get("max_tabs") or 1, priority=priority, on_wait=on_wait):
        for _attempt in range(3):
            state, url = wait_live(bs["id"])
            if state is None:
                note = _status_note(bs["id"])
                return {
                    "status": "failed", "stopped": False, "log": [],
                    "reason": (f"The ERP login for branch {bs['branch']} is not live"
                               + (f" ({note})" if note else "")
                               + ". Check it on the ERP Scripts page, then press Retry."),
                    "steps_done": 0, "steps_total": 0, "captured": {},
                }
            res = run({"state": state, "url": url})
            if not (isinstance(res, dict) and res.get("session_lost")):
                return res
            logger.warning("branch session %s (%s): login dropped under a job - logging in again",
                           bs["id"], bs["branch"])
            report_lost(bs["id"])
            time.sleep(5)  # let the keeper notice before we start polling for the new file
    return res


def _status_note(bs_id: str) -> str:
    from app.db.session import SessionLocal
    from app.models.erp_branch_session import ErpBranchSession

    db = SessionLocal()
    try:
        bs = db.get(ErpBranchSession, bs_id)
        return (bs.status_note or bs.status) if bs else ""
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------
# keeper side (singleton worker only)
# ---------------------------------------------------------------------------------------------

_USER_HINT = re.compile(r"user|login|email|uname", re.I)
_PASS_HINT = re.compile(r"pass|pwd", re.I)


def login_plan(script, bs) -> tuple[list[dict], dict, dict | None]:
    """(steps to replay, values, probe step) for logging this branch's user in.

    Steps are the login script's up to and including its checkpoint. The branch's own user ID
    and password replace the recorded ones: the fill step whose selector/label says password
    gets the password; the first other fill step before it that looks like a user field (or
    just the first fill step) gets the user ID. The branch goes in under the script's branch
    field (default "Destination") - the login screen's location box reads it from there."""
    from app.core.mail_crypto import decrypt_secret

    ckpt = script.checkpoint_index
    steps = copy.deepcopy((script.steps or [])[: (ckpt + 1)]) if ckpt is not None else []
    user = (bs.username or "").strip()
    pwd = ""
    if bs.password_encrypted:
        try:
            pwd = decrypt_secret(bs.password_encrypted)
        except Exception:  # noqa: BLE001
            pwd = ""
    if user or pwd:
        fills = [s for s in steps if s.get("action") == "fill"]

        def hay(s):
            return f"{s.get('selector') or ''} {s.get('description') or ''}"

        pstep = next((s for s in fills if _PASS_HINT.search(hay(s))), None)
        others = [s for s in fills if s is not pstep]
        ustep = next((s for s in others if _USER_HINT.search(hay(s))), others[0] if others else None)
        if user and ustep is not None:
            ustep.update({"value": user, "field_label": None, "prompt": None})
        if pwd and pstep is not None:
            pstep.update({"value": pwd, "field_label": None, "prompt": None})
    bfield = getattr(script, "branch_field", None) or "Destination"
    values = {bfield: bs.branch}
    # The step that picks the branch at login is only there for a user with several branches.
    # A user with ONE branch (a branch's own bot user - "4SCDCBILLING" sees Chennai as plain
    # text, no dropdown) has nothing to pick, and the step would fail the whole login. So it is
    # optional HERE - for the branch login only, never in the saved script - and _login checks
    # afterwards that the ERP really is on this branch.
    for s in steps:
        if (s.get("field_label") or "").strip().casefold() == bfield.strip().casefold():
            s["is_optional"] = True
    probe = None
    if ckpt is not None:
        probe = next((s for s in (script.steps or [])[ckpt + 1:] if s.get("selector")), None)
    return steps, values, probe


def _set_status(bs_id: str, status: str, note: str | None = None, *, checked=False, live=False) -> None:
    from app.db.session import SessionLocal
    from app.models.erp_branch_session import ErpBranchSession

    db = SessionLocal()
    try:
        bs = db.get(ErpBranchSession, bs_id)
        if bs is None:
            return
        now = datetime.now(timezone.utc)
        if live and bs.status != "live":
            bs.live_since = now
        if not live:
            bs.live_since = None
        bs.status = status
        bs.status_note = note
        if checked:
            bs.last_check_at = now
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not write the status of branch session %s", bs_id)
        db.rollback()
    finally:
        db.close()


def _config_key(bs, script) -> tuple:
    """Anything that changes HOW the branch logs in - a change restarts its keeper."""
    # NOT the script's updated_at: a keeper restart is a fresh login, which logs out every job
    # running on the branch - so only a change to how it logs in may cause one.
    return (bs.branch, bs.login_script_id, bs.username, bs.password_encrypted,
            script.url if script else None, script.checkpoint_index if script else None)


class _Keeper:
    def __init__(self, bs_id: str, key: tuple):
        self.bs_id = bs_id
        self.key = key
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"branch-keeper-{bs_id[:8]}", daemon=True)
        self._ses = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    # -- one login ------------------------------------------------------------------------
    def _load(self):
        from app.db.session import SessionLocal
        from app.models.erp_branch_session import ErpBranchSession
        from app.models.erp_script import ErpScript

        db = SessionLocal()
        try:
            bs = db.get(ErpBranchSession, self.bs_id)
            script = db.get(ErpScript, bs.login_script_id) if bs else None
            if bs is None or script is None:
                return None
            steps, values, probe = login_plan(script, bs)
            return {"branch": bs.branch, "url": script.url, "steps": steps, "values": values,
                    "probe": probe, "has_ckpt": script.checkpoint_index is not None}
        finally:
            db.close()

    def _close(self):
        if self._ses is not None:
            try:
                self._ses.close()
            except Exception:  # noqa: BLE001
                pass
            self._ses = None

    def _probe_ok(self, plan) -> bool:
        from app.core.browser import _scoped

        probe = plan["probe"]

        def look(p):
            if probe is None:
                return "/login" not in (p.url or "").lower()
            try:
                _scoped(p, probe.get("frames") or [], probe["selector"]).first.wait_for(
                    state="attached", timeout=15000)
                return True
            except Exception:  # noqa: BLE001
                return False

        try:
            return bool(self._ses.submit(look, timeout=60))
        except Exception:  # noqa: BLE001
            return False

    def _save(self) -> bool:
        from app.core.browser import save_checkpoint_session

        f = session_file(self.bs_id)
        try:
            return bool(self._ses.submit(lambda p: save_checkpoint_session(f, p.context, p.url), timeout=60))
        except Exception:  # noqa: BLE001
            return False

    def _login(self, plan) -> str | None:
        """Log the branch in. None on success, else what went wrong."""
        from app.core.browser import RecorderSession

        if not plan["has_ckpt"]:
            return "its login script has no checkpoint - mark the step where login ends"
        self._close()
        self._ses = RecorderSession(plan["url"], None, headless=True)
        r = self._ses.replay(plan["steps"], dict(plan["values"]))
        if (r or {}).get("index", 0) < len(plan["steps"]):
            last = [str(x) for x in (r or {}).get("log") or []][-1:] or ["login steps did not finish"]
            return f"login stopped at step {(r or {}).get('index', 0) + 1}: {last[0][:200]}"
        if not self._probe_ok(plan):
            return "logged in, but the screen after the checkpoint never appeared"
        # The branch step may have been skipped (single-branch user), and a user can be set up
        # on a different branch than this row says. Entering a job on the wrong branch is the
        # one thing this whole design exists to prevent, so the logged-in screen must name it.
        want = plan["branch"].strip().casefold()
        try:
            text = self._ses.submit(lambda p: p.inner_text("body"), timeout=30) or ""
        except Exception:  # noqa: BLE001
            text = ""
        if want not in text.casefold():
            return (f"logged in, but the ERP screen does not show the {plan['branch']} branch - "
                    "check that this user is set up for it")
        if not self._save():
            return "logged in, but the session could not be saved"
        return None

    def _check(self, plan) -> bool:
        """Reload the keeper's own tab (never a job's) and make sure it still reaches the entry
        screen. Also what keeps an idle login from timing out on the ERP side."""
        try:
            self._ses.submit(lambda p: (p.reload(wait_until="domcontentloaded", timeout=45000),
                                        p.wait_for_timeout(3000)), timeout=90)
        except Exception:  # noqa: BLE001
            return False
        return self._probe_ok(plan) and self._save()

    # -- the loop -------------------------------------------------------------------------
    def _run(self):
        from app.core.browser import discard_checkpoint_session

        fails = 0
        while not self.stop_event.is_set():
            plan = self._load()
            if plan is None:
                return
            _set_status(self.bs_id, "logging_in", f"logging in to {plan['branch']}")
            _relogin_flag(self.bs_id).unlink(missing_ok=True)
            try:
                why = self._login(plan)
            except Exception as exc:  # noqa: BLE001
                why = f"login crashed: {exc}"
            if why:
                self._close()
                discard_checkpoint_session(session_file(self.bs_id))
                wait = RELOGIN_BACKOFF[min(fails, len(RELOGIN_BACKOFF) - 1)]
                fails += 1
                logger.warning("branch session %s (%s): %s - next try in %ss",
                               self.bs_id, plan["branch"], why, wait)
                _set_status(self.bs_id, "error", f"{why} - trying again in {wait // 60 or 1} min")
                if self.stop_event.wait(wait):
                    break
                continue
            fails = 0
            logger.info("branch session %s (%s): live", self.bs_id, plan["branch"])
            _set_status(self.bs_id, "live", None, checked=True, live=True)
            next_check = time.monotonic() + CHECK_SECONDS
            # Live: check every CHECK_SECONDS, or at once when a job / the Super Admin says
            # the login is gone.
            while not self.stop_event.wait(3):
                flagged = _relogin_flag(self.bs_id).exists()
                if not flagged and time.monotonic() < next_check:
                    continue
                # A fresh login logs out every job on this branch, so one failed look is not
                # enough to throw the login away - a slow ERP page gets a second look first.
                ok = self._check(plan) or (not self.stop_event.wait(20) and self._check(plan))
                if ok:
                    # Still reaches the entry screen (and has just been saved again). A job
                    # that flagged it hit a slow page, not a lost login.
                    _relogin_flag(self.bs_id).unlink(missing_ok=True)
                    _set_status(self.bs_id, "live", None, checked=True, live=True)
                    next_check = time.monotonic() + CHECK_SECONDS
                    continue
                discard_checkpoint_session(session_file(self.bs_id))
                _set_status(self.bs_id, "logging_in", "the login dropped - logging in again")
                break
        self._close()
        discard_checkpoint_session(session_file(self.bs_id))
        _set_status(self.bs_id, "off", None)


def _sync_keepers() -> None:
    from app.db.session import SessionLocal
    from app.models.erp_branch_session import ErpBranchSession
    from app.models.erp_script import ErpScript

    db = SessionLocal()
    try:
        want: dict[str, tuple] = {}
        for bs in db.query(ErpBranchSession).filter(ErpBranchSession.enabled.is_(True)).all():
            want[bs.id] = _config_key(bs, db.get(ErpScript, bs.login_script_id))
        off = [bs.id for bs in db.query(ErpBranchSession).filter(ErpBranchSession.enabled.is_(False)).all()]
    finally:
        db.close()
    with _keepers_guard:
        for bs_id, k in list(_keepers.items()):
            if bs_id not in want or k.key != want[bs_id] or not k.thread.is_alive():
                k.stop()
                del _keepers[bs_id]
        for bs_id, key in want.items():
            if bs_id not in _keepers:
                k = _Keeper(bs_id, key)
                _keepers[bs_id] = k
                k.start()
    for bs_id in off:
        if bs_id not in _keepers:
            _set_status_if(bs_id, "off")


def _set_status_if(bs_id: str, status: str) -> None:
    from app.db.session import SessionLocal
    from app.models.erp_branch_session import ErpBranchSession

    db = SessionLocal()
    try:
        bs = db.get(ErpBranchSession, bs_id)
        if bs is not None and bs.status != status:
            bs.status, bs.status_note, bs.live_since = status, None, None
            db.commit()
    finally:
        db.close()


def start() -> None:
    """Start the supervisor (singleton worker only). Idempotent."""
    global _supervisor
    if _supervisor is not None and _supervisor.is_alive():
        return
    # Files left by the previous process name logins whose keeper browsers are gone, and each
    # keeper is about to log its branch in afresh - which ends those sessions on the ERP side.
    # A job handed one now would start on a login seconds from being replaced, so jobs wait
    # for the new one instead.
    for f in _dir().glob("branch_*.json"):
        f.unlink(missing_ok=True)

    def loop():
        while not _stop.is_set():
            try:
                _sync_keepers()
            except Exception:  # noqa: BLE001
                logger.exception("branch session supervisor pass failed")
            _stop.wait(SUPERVISE_SECONDS)

    _supervisor = threading.Thread(target=loop, name="branch-sessions", daemon=True)
    _supervisor.start()
