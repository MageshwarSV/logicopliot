"""One ERP login shared by every job running at once (app/core/erp_session.py).

The thing being protected is not speed, it is the OTHER job's session. SOFTLINK answers a
second login with "you are logged in from another portal, kill the other session?", so a job
that logs in while another is mid-entry can take that other job's screen away. With
erp_script_workers > 1 that stops being theoretical: jobs approved in the same minute start
together and all find no saved session.

So exactly one job logs in and the rest wait for it. These tests drive that broker directly -
no browser, no ERP - by writing session files the way a real checkpoint save would.
"""
import json
import threading
import time

import pytest

from app.core import erp_session


def _write_session(path, *, age=0.0, url="https://erp.example/entry", state=None):
    """A session file in exactly the shape save_checkpoint_session writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "saved_at": time.time() - age,
        "url": url,
        "state": state or {"cookies": [{"name": "ASP.NET_SessionId", "value": "abc"}]},
    }), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    """Real waits are 120s/0.5s. Keep the behaviour, shrink the clock."""
    monkeypatch.setattr(erp_session, "LOGIN_WAIT_SECONDS", 3)
    monkeypatch.setattr(erp_session, "_POLL_SECONDS", 0.05)
    # Each test gets its own in-process lock table, or a lock left held by a previous test
    # would make the next one look like it was waiting on a leader.
    monkeypatch.setattr(erp_session, "_local_locks", {})


def test_a_live_session_is_handed_straight_back(tmp_path):
    """The common case once the first job has logged in: no lock, no login, no wait."""
    f = tmp_path / "s.json"
    _write_session(f, age=10)
    lease = erp_session.acquire(f)
    try:
        assert lease.state is not None
        assert lease.url == "https://erp.example/entry"
        assert lease.leader is False, "a job that found a session must not hold the login lock"
    finally:
        lease.release()


def test_first_job_with_no_session_becomes_the_one_that_logs_in(tmp_path):
    f = tmp_path / "s.json"
    lease = erp_session.acquire(f)
    try:
        assert lease.leader is True
        assert lease.state is None, "nothing to resume from - it has to replay the login"
    finally:
        lease.release()


def test_an_expired_session_is_not_reused(tmp_path):
    """Past CHECKPOINT_MAX_AGE (30 min) the ERP has almost certainly dropped it."""
    f = tmp_path / "s.json"
    _write_session(f, age=3600)
    lease = erp_session.acquire(f)
    try:
        assert lease.state is None
        assert lease.leader is True
    finally:
        lease.release()


def test_the_second_job_waits_for_the_first_login_instead_of_starting_its_own(tmp_path):
    """The whole point. Job A is logging in; job B must NOT open a second login."""
    f = tmp_path / "s.json"
    a = erp_session.acquire(f)
    assert a.leader is True

    seen = {}

    def job_b():
        t0 = time.time()
        b = erp_session.acquire(f)
        seen["leader"] = b.leader
        seen["state"] = b.state
        seen["waited"] = time.time() - t0
        b.release()

    t = threading.Thread(target=job_b)
    t.start()
    time.sleep(0.3)                      # B is now waiting on A
    _write_session(f)                    # A reaches the checkpoint and saves
    a.published()                        # ... and wakes the waiters
    t.join(timeout=5)
    assert not t.is_alive()

    assert seen["leader"] is False, "job B started its own login while job A held it"
    assert seen["state"] is not None, "job B did not pick up the session job A published"
    assert seen["waited"] < 3, "job B sat out the whole timeout instead of being woken"


def test_a_leader_that_dies_without_publishing_hands_the_login_on(tmp_path):
    """A crashed run releases in its `finally`; the next job logs in rather than waiting
    forever behind a leader that no longer exists."""
    f = tmp_path / "s.json"
    a = erp_session.acquire(f)
    assert a.leader is True
    a.release()                          # the run crashed; no session was ever saved

    b = erp_session.acquire(f)
    try:
        assert b.leader is True, "nobody picked up the login after the leader died"
    finally:
        b.release()


def test_a_waiter_takes_over_when_the_leader_gives_up_mid_wait(tmp_path):
    f = tmp_path / "s.json"
    a = erp_session.acquire(f)
    assert a.leader is True

    seen = {}

    def job_b():
        b = erp_session.acquire(f)
        seen["leader"] = b.leader
        b.release()

    t = threading.Thread(target=job_b)
    t.start()
    time.sleep(0.3)
    a.release()                          # login failed, nothing published
    t.join(timeout=5)
    assert not t.is_alive()
    assert seen["leader"] is True, "the waiter should have taken over the login"


def test_waiting_gives_up_rather_than_blocking_a_job_forever(tmp_path):
    """A wedged leader costs one job a redundant login. It must never cost it the job."""
    f = tmp_path / "s.json"
    a = erp_session.acquire(f)
    assert a.leader is True
    try:
        t0 = time.time()
        b = erp_session.acquire(f)       # leader never publishes, never releases
        waited = time.time() - t0
        try:
            assert b.leader is False
            assert b.state is None, "it has to log in itself"
            assert 3 <= waited < 8, f"waited {waited:.1f}s, expected about LOGIN_WAIT_SECONDS"
        finally:
            b.release()
    finally:
        a.release()


def test_two_different_scripts_never_wait_on_each_other(tmp_path):
    """Different scripts are different ERP accounts - there is nothing to serialise."""
    one = erp_session.acquire(tmp_path / "script-a.json")
    two = erp_session.acquire(tmp_path / "script-b.json")
    try:
        assert one.leader is True and two.leader is True
    finally:
        one.release()
        two.release()


def test_touch_keeps_a_busy_script_from_ageing_out_its_working_session(tmp_path):
    """A session 29 minutes old that still reaches the entry screen is alive, and the next
    job should resume it rather than open the login that can kill this one."""
    f = tmp_path / "s.json"
    _write_session(f, age=29 * 60)
    lease = erp_session.acquire(f)
    assert lease.state is not None
    lease.touch()                        # the probe passed - this session works right now
    lease.release()

    # Two minutes later it would have been 31 minutes old and thrown away. It is not.
    blob = json.loads(f.read_text(encoding="utf-8"))
    assert time.time() - blob["saved_at"] < 5
    again = erp_session.acquire(f)
    try:
        assert again.state is not None, "the refreshed session was discarded anyway"
        assert again.leader is False
    finally:
        again.release()


def test_touch_keeps_the_url_and_cookies_it_was_given(tmp_path):
    """Refreshing the timestamp must not quietly rewrite what resuming depends on."""
    f = tmp_path / "s.json"
    _write_session(f, age=100, url="https://erp.example/Default.aspx?x=1",
                   state={"cookies": [{"name": "k", "value": "v"}], "origins": []})
    lease = erp_session.acquire(f)
    lease.touch()
    lease.release()

    blob = json.loads(f.read_text(encoding="utf-8"))
    assert blob["url"] == "https://erp.example/Default.aspx?x=1"
    assert blob["state"]["cookies"] == [{"name": "k", "value": "v"}]


def test_no_session_file_at_all_is_a_lease_that_does_nothing(tmp_path):
    """Scripts without a checkpoint never had a shared session, and still must not break."""
    lease = erp_session.acquire(None)
    lease.touch()          # all of these are no-ops
    lease.published()
    lease.release()
    assert lease.state is None and lease.leader is False


def test_a_half_written_session_file_is_never_handed_to_a_job(tmp_path):
    """save_checkpoint_session truncates before it writes. A reader landing in that window
    used to get an unparseable file; _atomic_write renames into place instead."""
    f = tmp_path / "s.json"
    _write_session(f, age=10)
    good = f.read_text(encoding="utf-8")

    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            try:
                text = f.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                continue          # Windows can refuse mid-rename; it is not a torn read
            if text and text != good:
                try:
                    json.loads(text)
                except ValueError:
                    bad.append(text)

    t = threading.Thread(target=reader)
    t.start()
    for _ in range(60):
        erp_session.atomic_write(f, good)
    stop.set()
    t.join(timeout=5)
    assert not bad, f"a reader saw {len(bad)} partially-written session file(s)"
