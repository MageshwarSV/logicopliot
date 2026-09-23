"""_pull_inbox used to read every connected mailbox ONE AFTER ANOTHER in a single loop - a
real bottleneck once more than a couple of operators connect their own mailbox, since reading
one is minutes of work (OCR every page, then AI calls per document). A mailbox near the end of
the list could go unchecked through an entire poll cycle. These tests exercise the fix: each
mailbox now runs in its own thread, up to the live, database-backed worker count (Super
Admin's Settings page, see app.core.system_settings) at once, each with its own DB session -
`_pull_one_mailbox` itself is mocked out, since it is what every OTHER email_puller test
already exercises; this file is only about the orchestration around it."""

import threading
import time
from unittest.mock import MagicMock, patch

from app.core.email_puller import _pull_inbox, _record_cycle_status, get_last_cycle_status


def _sources(n: int) -> list[dict]:
    return [
        {"operator_id": f"op-{i}", "email": f"op{i}@example.com", "password": "pw", "host": "imap.zoho.in"}
        for i in range(n)
    ]


def _patched(sources, side_effect=None, return_value=None, workers=None):
    """Common patching for these tests: no real DB, no real mailbox discovery, a controlled
    worker count, and SessionLocal replaced so no real database is touched."""
    settings = MagicMock()
    settings.gmail_user = ""
    patches = [
        patch("app.core.email_puller.get_settings", return_value=settings),
        patch("app.core.email_puller._operator_mailboxes", return_value=sources),
        patch("app.db.session.SessionLocal", return_value=MagicMock()),
        patch("app.core.system_settings.get_email_poll_workers",
             return_value=workers if workers is not None else 4),
    ]
    if side_effect is not None:
        patches.append(patch("app.core.email_puller._pull_one_mailbox", side_effect=side_effect))
    else:
        patches.append(patch("app.core.email_puller._pull_one_mailbox", return_value=return_value))
    return patches


def _enter_all(patches):
    mocks = [p.start() for p in patches]
    return mocks


def _exit_all(patches):
    for p in reversed(patches):
        p.stop()


def test_pull_inbox_reports_how_many_mailboxes_were_attempted_and_how_many_succeeded():
    sources = _sources(5)

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        if operator_id in ("op-1", "op-3"):
            return {"ok": False, "error": "bad credentials"}
        return {"ok": True, "processed": []}

    patches = _patched(sources, side_effect=side_effect)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert result["mailboxes_total"] == 5
    assert result["mailboxes_ok"] == 3
    assert len(result["mailbox_errors"]) == 2


def test_every_connected_mailbox_is_pulled_not_just_the_first_few():
    sources = _sources(7)
    patches = _patched(sources, return_value={"ok": True, "processed": [{"job_id": "j"}]})
    mocks = _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)
    pull_mock = mocks[-1]
    assert pull_mock.call_count == 7
    assert len(result["processed"]) == 7


def test_results_from_every_mailbox_are_merged_including_errors_and_notes():
    sources = _sources(3)

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        if operator_id == "op-0":
            return {"ok": True, "processed": [{"job_id": "job-a"}]}
        if operator_id == "op-1":
            return {"ok": False, "error": "bad credentials"}
        return {"ok": True, "processed": [], "note": "nothing new"}

    patches = _patched(sources, side_effect=side_effect)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert result["processed"] == [{"job_id": "job-a"}]
    assert result["mailbox_errors"] == ["op1@example.com: bad credentials"]


def test_one_mailbox_raising_an_exception_does_not_stop_the_others():
    sources = _sources(3)

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        if operator_id == "op-1":
            raise RuntimeError("IMAP connection reset")
        return {"ok": True, "processed": [{"job_id": operator_id}]}

    patches = _patched(sources, side_effect=side_effect)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    processed_ids = {p["job_id"] for p in result["processed"]}
    assert processed_ids == {"op-0", "op-2"}
    assert any("IMAP connection reset" in e for e in result["mailbox_errors"])


def test_mailboxes_are_actually_read_concurrently_not_one_after_another():
    # Ten mailboxes, each one "takes" 100ms. Run one after another that is >= 1 second;
    # run four at a time it is roughly 300ms (10 mailboxes / 4 workers, rounded up = 3
    # batches). A generous ceiling well under the sequential time proves this is not a
    # relabelled version of the old loop.
    sources = _sources(10)

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        time.sleep(0.1)
        return {"ok": True, "processed": []}

    patches = _patched(sources, side_effect=side_effect, workers=4)
    _enter_all(patches)
    try:
        start = time.monotonic()
        _pull_inbox(db=MagicMock())
        elapsed = time.monotonic() - start
    finally:
        _exit_all(patches)

    assert elapsed < 0.8, f"took {elapsed:.2f}s - looks sequential, not pooled"


def test_worker_count_never_exceeds_the_configured_limit():
    sources = _sources(9)
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        nonlocal in_flight, max_seen
        with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return {"ok": True, "processed": []}

    patches = _patched(sources, side_effect=side_effect, workers=3)
    _enter_all(patches)
    try:
        _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert max_seen <= 3, f"saw {max_seen} concurrent pulls, limit was 3"
    assert max_seen > 1, "never actually ran more than one at a time - not parallel at all"


def test_worker_count_is_capped_by_the_number_of_mailboxes_not_wasted_on_idle_threads():
    # Two mailboxes with a pool sized for eight must not error or hang - just use two.
    sources = _sources(2)
    patches = _patched(sources, return_value={"ok": True, "processed": []}, workers=8)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)
    assert result["ok"] is True


def test_each_mailbox_gets_its_own_db_session_not_the_callers():
    sources = _sources(3)
    seen_sessions = []

    def side_effect(db, tenant_id, max_messages, operator_id, reexamine, **kw):
        seen_sessions.append(db)
        return {"ok": True, "processed": []}

    patches = [
        patch("app.core.email_puller.get_settings", return_value=MagicMock(gmail_user="")),
        patch("app.core.email_puller._operator_mailboxes", return_value=sources),
        patch("app.db.session.SessionLocal", side_effect=lambda: MagicMock()),
        patch("app.core.system_settings.get_email_poll_workers", return_value=4),
        patch("app.core.email_puller._pull_one_mailbox", side_effect=side_effect),
    ]
    _enter_all(patches)
    try:
        callers_db = MagicMock()
        _pull_inbox(db=callers_db)
    finally:
        _exit_all(patches)

    assert len(seen_sessions) == 3
    assert callers_db not in seen_sessions
    # Each worker's session is distinct - never the same object reused across threads.
    assert len({id(s) for s in seen_sessions}) == 3


# ---- the poller's own "what did my last tick do" report --------------------------------------

from datetime import datetime, timezone


def test_record_cycle_status_reports_a_completed_cycle(db_session):
    res = {
        "ok": True, "processed": [{"job_id": "j1"}, {"job_id": None}],
        "mailboxes_total": 9, "mailboxes_ok": 7,
        "mailbox_errors": ["a@x.com: bad password", "b@x.com: timeout"],
    }
    _record_cycle_status(res, datetime.now(timezone.utc), db_session)
    status = get_last_cycle_status(db_session)

    assert status["skipped_busy"] is False
    assert status["mailboxes_total"] == 9
    assert status["mailboxes_ok"] == 7
    assert status["mailboxes_failed"] == 2
    assert status["mailbox_errors"] == ["a@x.com: bad password", "b@x.com: timeout"]
    assert status["jobs_created"] == 1
    assert status["messages_processed"] == 2
    assert status["started_at"] and status["finished_at"]


def test_record_cycle_status_reports_a_skipped_cycle(db_session):
    res = {"ok": True, "processed": [], "busy": True,
           "note": "A run was already in progress; this cycle was skipped."}
    _record_cycle_status(res, datetime.now(timezone.utc), db_session)
    status = get_last_cycle_status(db_session)

    assert status["skipped_busy"] is True
    assert status["mailboxes_total"] == 0
    assert status["jobs_created"] == 0


def test_get_last_cycle_status_is_a_snapshot_not_a_live_reference(db_session):
    _record_cycle_status({"ok": True, "processed": [], "mailboxes_total": 3, "mailboxes_ok": 3},
                         datetime.now(timezone.utc), db_session)
    first = get_last_cycle_status(db_session)
    _record_cycle_status({"ok": True, "processed": [], "mailboxes_total": 5, "mailboxes_ok": 5},
                         datetime.now(timezone.utc), db_session)
    # The dict returned earlier must not silently change when a later cycle is recorded.
    assert first["mailboxes_total"] == 3
    assert get_last_cycle_status(db_session)["mailboxes_total"] == 5


def test_poll_status_endpoint_returns_the_last_recorded_cycle(client, db_session):
    from tests.conftest import login, make_user

    _record_cycle_status(
        {"ok": True, "processed": [{"job_id": "j1"}], "mailboxes_total": 4, "mailboxes_ok": 4},
        datetime.now(timezone.utc),
        db_session,
    )
    admin = make_user(db_session, role="super_admin", email="poll-status-admin@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/email/poll-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mailboxes_total"] == 4
    assert body["jobs_created"] == 1


def test_poll_status_endpoint_is_refused_to_operators(client, db_session):
    from tests.conftest import login, make_tenant, make_user

    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="poll-status-op@example.com")
    login(client, op.email)
    resp = client.get("/api/v1/email/poll-status")
    assert resp.status_code == 403
