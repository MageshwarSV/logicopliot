"""_pull_inbox reads every connected mailbox through two phases now, not one thread per
mailbox doing everything end to end:

  1. harvest every mailbox concurrently (cheap - an IMAP search and a PEEK fetch, not OCR),
     one thread per mailbox, via _mailbox_setup + _harvest_mailbox.
  2. process every harvested message - OCR plus the AI calls, the expensive part - through
     ONE SHARED pool, via _process_one_message. A worker that finishes a quiet mailbox's one
     message does not sit idle while a busier mailbox still has a backlog; it picks up the
     next task off the SAME queue regardless of which mailbox it came from.

`_mailbox_setup`, `_harvest_mailbox` and `_process_one_message` are mocked out in most of
these tests, since the per-message pipeline itself is what every OTHER email_puller test
already exercises (via `_pull_one_mailbox`, which is just a sequential composition of these
same three pieces) - this file is only about the orchestration around them, and specifically
about proving idle capacity actually flows to a busier mailbox instead of sitting unused."""

import threading
import time
from unittest.mock import MagicMock, patch

from app.core.email_puller import _pull_inbox, _record_cycle_status, get_last_cycle_status


def _sources(n: int) -> list[dict]:
    return [
        {"mailbox_id": f"mb-{i}", "email": f"op{i}@example.com", "password": "pw",
         "host": "imap.zoho.in", "tenant_id": f"tenant-{i}", "group_id": None}
        for i in range(n)
    ]


def _ctx(unidentifiable=None):
    return {"single_group": None, "candidates": [], "by_id": {},
            "unidentifiable": unidentifiable or []}


def _patched(sources, setup_side_effect=None, harvest_side_effect=None,
            harvest_return=None, process_side_effect=None, process_return=None,
            workers=None):
    """Common patching for these tests: no real DB, no real mailbox discovery, a controlled
    worker count, and SessionLocal replaced so no real database is touched."""
    settings = MagicMock()
    settings.gmail_user = ""
    patches = [
        patch("app.core.email_puller.get_settings", return_value=settings),
        patch("app.core.email_puller._connected_mailboxes", return_value=sources),
        patch("app.db.session.SessionLocal", return_value=MagicMock()),
        patch("app.core.system_settings.get_email_poll_workers",
             return_value=workers if workers is not None else 4),
        patch("app.core.system_settings.get_extraction_engine", return_value="ocr_gpt4o_mini"),
        patch("app.core.custom_page_filter.get_active_custom_filter_texts", return_value=[]),
    ]
    if setup_side_effect is not None:
        patches.append(patch("app.core.email_puller._mailbox_setup", side_effect=setup_side_effect))
    else:
        patches.append(patch("app.core.email_puller._mailbox_setup", return_value=(_ctx(), None)))
    if harvest_side_effect is not None:
        patches.append(patch("app.core.email_puller._harvest_mailbox", side_effect=harvest_side_effect))
    else:
        patches.append(patch("app.core.email_puller._harvest_mailbox",
                             return_value=harvest_return if harvest_return is not None
                             else {"ok": True, "messages": []}))
    if process_side_effect is not None:
        patches.append(patch("app.core.email_puller._process_one_message", side_effect=process_side_effect))
    elif process_return is not None:
        patches.append(patch("app.core.email_puller._process_one_message", return_value=process_return))
    return patches


def _enter_all(patches):
    mocks = [p.start() for p in patches]
    return mocks


def _exit_all(patches):
    for p in reversed(patches):
        p.stop()


def test_pull_inbox_reports_how_many_mailboxes_were_attempted_and_how_many_succeeded():
    sources = _sources(5)

    def harvest(mail_email, mail_app_password, mail_host, max_messages, reexamine):
        if mail_email in ("op1@example.com", "op3@example.com"):
            return {"ok": False, "error": "bad credentials"}
        return {"ok": True, "messages": []}

    patches = _patched(sources, harvest_side_effect=harvest)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert result["mailboxes_total"] == 5
    assert result["mailboxes_ok"] == 3
    assert len(result["mailbox_errors"]) == 2


def test_every_connected_mailbox_is_harvested_not_just_the_first_few():
    sources = _sources(7)
    patches = _patched(sources, harvest_return={"ok": True, "messages": []})
    mocks = _enter_all(patches)
    try:
        _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)
    # _mailbox_setup and _harvest_mailbox are the last two patches started (see _patched).
    setup_mock, harvest_mock = mocks[-2], mocks[-1]
    assert setup_mock.call_count == 7
    assert harvest_mock.call_count == 7


def test_results_from_every_mailbox_are_merged_including_errors_and_notes():
    sources = _sources(3)

    def harvest(mail_email, mail_app_password, mail_host, max_messages, reexamine):
        if mail_email == "op0@example.com":
            return {"ok": True, "messages": [{"uid": b"1", "raw": b"raw-a"}]}
        if mail_email == "op1@example.com":
            return {"ok": False, "error": "bad credentials"}
        return {"ok": True, "messages": []}

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        return {"from": "a@example.com", "subject": "x", "matched": "Customer A",
                "job_id": "job-a"}

    patches = _patched(sources, harvest_side_effect=harvest, process_side_effect=process)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert result["processed"] == [{"from": "a@example.com", "subject": "x",
                                    "matched": "Customer A", "job_id": "job-a"}]
    assert result["mailbox_errors"] == ["op1@example.com: bad credentials"]


def test_one_mailbox_raising_during_harvest_does_not_stop_the_others():
    sources = _sources(3)

    def harvest(mail_email, mail_app_password, mail_host, max_messages, reexamine):
        if mail_email == "op1@example.com":
            raise RuntimeError("IMAP connection reset")
        return {"ok": True, "messages": [{"uid": b"1", "raw": b"raw"}]}

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        return {"from": mail_email, "subject": "x", "matched": "C", "job_id": mail_email}

    patches = _patched(sources, harvest_side_effect=harvest, process_side_effect=process)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    processed_ids = {p["job_id"] for p in result["processed"]}
    assert processed_ids == {"op0@example.com", "op2@example.com"}
    assert any("IMAP connection reset" in e for e in result["mailbox_errors"])


def test_one_message_crashing_during_processing_does_not_stop_sibling_messages():
    # A bug mid-pipeline for ONE message must not take down any other message - including
    # ones from the very same mailbox, now that they can run on different workers.
    sources = _sources(1)
    messages = [{"uid": f"{i}".encode(), "raw": f"raw-{i}".encode()} for i in range(4)]

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        if uid == b"2":
            raise RuntimeError("boom")
        return {"from": "x", "subject": "x", "matched": "C", "job_id": uid.decode()}

    patches = _patched(sources, harvest_return={"ok": True, "messages": messages},
                       process_side_effect=process)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    job_ids = {p["job_id"] for p in result["processed"] if p.get("job_id")}
    assert job_ids == {"0", "1", "3"}
    # The crash comes back as its own entry in `processed` (an "error" flag and a reason
    # naming the exception) rather than as a mailbox-level failure - a single message
    # blowing up is not "the mailbox couldn't be read", so it must not be charged against
    # mailboxes_ok, and it must not be silently dropped either.
    errored = [p for p in result["processed"] if p.get("error")]
    assert len(errored) == 1
    assert "boom" in errored[0]["reason"]
    assert result["mailboxes_ok"] == 1
    assert "mailbox_errors" not in result


def test_mailboxes_are_harvested_concurrently_not_one_after_another():
    # Ten mailboxes, each one "takes" 100ms to harvest. Sequential would be >= 1 second;
    # four at a time is roughly 300ms (10 / 4 workers, rounded up = 3 batches). A generous
    # ceiling well under the sequential time proves this is not a relabelled sequential loop.
    sources = _sources(10)

    def harvest(mail_email, mail_app_password, mail_host, max_messages, reexamine):
        time.sleep(0.1)
        return {"ok": True, "messages": []}

    patches = _patched(sources, harvest_side_effect=harvest, workers=4)
    _enter_all(patches)
    try:
        start = time.monotonic()
        _pull_inbox(db=MagicMock())
        elapsed = time.monotonic() - start
    finally:
        _exit_all(patches)

    assert elapsed < 0.8, f"took {elapsed:.2f}s - looks sequential, not pooled"


def test_an_idle_mailboxs_worker_helps_a_busier_mailboxs_backlog():
    # THE fix this redesign exists for. Mailbox A has 1 message; mailbox B has 5. Each
    # message "takes" 150ms to process. With the OLD one-thread-per-mailbox design (2
    # mailboxes -> at most 2 workers regardless of how many are configured), A's thread
    # finishes after one message and sits idle while B's thread serially works through its
    # five: total wall-clock ~= 5 * 150ms = 750ms. With a SHARED pool across all 6 harvested
    # messages and workers=2, the worker that finishes A's one message immediately helps
    # with B's backlog: total wall-clock ~= ceil(6 / 2) * 150ms = 450ms. A ceiling comfortably
    # between the two (and well under the old-design bound) proves work actually moved across
    # the mailbox boundary rather than staying pinned to whichever mailbox harvested it.
    sources = _sources(2)
    harvests = {
        "op0@example.com": {"ok": True, "messages": [{"uid": b"a0", "raw": b"a0"}]},
        "op1@example.com": {"ok": True, "messages": [
            {"uid": f"b{i}".encode(), "raw": f"b{i}".encode()} for i in range(5)
        ]},
    }

    def harvest(mail_email, mail_app_password, mail_host, max_messages, reexamine):
        return harvests[mail_email]

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        time.sleep(0.15)
        return {"from": mail_email, "subject": "x", "matched": "C", "job_id": uid.decode()}

    patches = _patched(sources, harvest_side_effect=harvest, process_side_effect=process,
                       workers=2)
    _enter_all(patches)
    try:
        start = time.monotonic()
        result = _pull_inbox(db=MagicMock())
        elapsed = time.monotonic() - start
    finally:
        _exit_all(patches)

    assert len(result["processed"]) == 6
    assert elapsed < 0.7, (
        f"took {elapsed:.2f}s - a quiet mailbox's worker was never put to work on the "
        "busy mailbox's backlog (old one-thread-per-mailbox behaviour would take ~0.75s)"
    )


def test_worker_count_never_exceeds_the_configured_limit_during_processing():
    sources = _sources(1)
    messages = [{"uid": f"{i}".encode(), "raw": f"{i}".encode()} for i in range(9)]
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        nonlocal in_flight, max_seen
        with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return {"from": "x", "subject": "x", "matched": "C"}

    patches = _patched(sources, harvest_return={"ok": True, "messages": messages},
                       process_side_effect=process, workers=3)
    _enter_all(patches)
    try:
        _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)

    assert max_seen <= 3, f"saw {max_seen} concurrent message workers, limit was 3"
    assert max_seen > 1, "never actually ran more than one message at a time - not parallel at all"


def test_processing_worker_count_is_capped_by_total_messages_not_mailbox_count():
    # Two mailboxes, one message each, a pool sized for eight must not error or hang - just
    # use two processing workers, not eight idle ones.
    sources = _sources(2)
    messages = [{"uid": b"only", "raw": b"only"}]
    patches = _patched(sources, harvest_return={"ok": True, "messages": messages},
                       process_return={"from": "x", "subject": "x", "matched": "C"},
                       workers=8)
    _enter_all(patches)
    try:
        result = _pull_inbox(db=MagicMock())
    finally:
        _exit_all(patches)
    assert result["ok"] is True
    assert len(result["processed"]) == 2


def test_each_message_gets_its_own_db_session_not_the_callers_or_each_others():
    sources = _sources(1)
    messages = [{"uid": f"{i}".encode(), "raw": f"{i}".encode()} for i in range(3)]
    seen_sessions = []
    lock = threading.Lock()

    def process(db, uid, raw, ctx, mail_email, mail_app_password, mail_host,
               extraction_engine, vision_engine_model, custom_filter_texts, reexamine):
        with lock:
            seen_sessions.append(db)
        return {"from": "x", "subject": "x", "matched": "C"}

    patches = [
        patch("app.core.email_puller.get_settings", return_value=MagicMock(gmail_user="")),
        patch("app.core.email_puller._connected_mailboxes", return_value=sources),
        patch("app.db.session.SessionLocal", side_effect=lambda: MagicMock()),
        patch("app.core.system_settings.get_email_poll_workers", return_value=4),
        patch("app.core.system_settings.get_extraction_engine", return_value="ocr_gpt4o_mini"),
        patch("app.core.custom_page_filter.get_active_custom_filter_texts", return_value=[]),
        patch("app.core.email_puller._mailbox_setup", return_value=(_ctx(), None)),
        patch("app.core.email_puller._harvest_mailbox", return_value={"ok": True, "messages": messages}),
        patch("app.core.email_puller._process_one_message", side_effect=process),
    ]
    _enter_all(patches)
    try:
        callers_db = MagicMock()
        _pull_inbox(db=callers_db)
    finally:
        _exit_all(patches)

    assert len(seen_sessions) == 3
    assert callers_db not in seen_sessions
    # Each message's session is distinct - never the same object reused across workers.
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
