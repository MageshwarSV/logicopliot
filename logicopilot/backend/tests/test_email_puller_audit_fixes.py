"""Three bugs found by an independent code audit of app/core/email_puller.py:

1. _extract_attachments treated an inline-embedded signature image (Content-Disposition:
   inline, a valid extension) as a real attachment - the two guard conditions it had were
   redundant with each other, so Content-Disposition was never actually consulted.

2. _claim's `steal` parameter, meant only to let reexamine reopen a FINISHED verdict, also
   bypassed the staleness check for a claim that is actively "working" right now - a
   reexamine call racing the background poller's own in-flight claim (0 minutes old) could
   steal it immediately, causing two runs to read the same message and build two jobs.

3. A message that succeeds on a later reexamine (after failing to match earlier) left its
   PendingEmail row sitting as "pending" forever - nothing cross-referenced the two - so a
   Super Admin resolving the stale pending item by hand later built a SECOND job for the
   exact same original email.
"""
import email
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.core.email_puller import CLAIM_STALE_MINUTES, _claim, _close_any_pending_email_for, _extract_attachments
from app.models.email_seen import EmailSeen
from app.models.pending_email import PendingEmail
from tests.conftest import make_tenant


# ---- _extract_attachments ----------------------------------------------------------------

def test_a_real_attachment_is_kept():
    msg = EmailMessage()
    msg["Subject"] = "Invoice"
    msg.set_content("Please see attached.")
    msg.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf", filename="invoice.pdf")

    files = _extract_attachments(msg)
    assert [name for name, _ in files] == ["invoice.pdf"]


def test_an_inline_signature_image_is_not_treated_as_an_attachment():
    msg = EmailMessage()
    msg["Subject"] = "Hello"
    msg.set_content("Regards,\nJohn")
    msg.add_attachment(b"\x89PNG-fake-logo-bytes", maintype="image", subtype="png",
                       filename="logo.png", disposition="inline")

    files = _extract_attachments(msg)
    assert files == []


def test_a_real_attachment_survives_alongside_an_inline_image():
    msg = EmailMessage()
    msg["Subject"] = "Invoice with signature"
    msg.set_content("See attached invoice.")
    msg.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf", filename="invoice.pdf")
    msg.add_attachment(b"\x89PNG-fake", maintype="image", subtype="png",
                       filename="logo.png", disposition="inline")

    files = _extract_attachments(msg)
    assert [name for name, _ in files] == ["invoice.pdf"]


# ---- _claim: steal must never bypass staleness for an in-flight claim ---------------------

def _seed_working_claim(db_session, message_id: str, minutes_old: float) -> EmailSeen:
    row = EmailSeen(
        message_id=message_id, sender="a@b.com", subject="hi", verdict="working",
        note="being read now",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    db_session.commit()
    return row


def test_steal_cannot_take_a_claim_that_is_actively_being_worked(db_session):
    make_tenant(db_session)
    _seed_working_claim(db_session, "msg-fresh@example.com", minutes_old=0)

    claimed, why = _claim(db_session, "msg-fresh@example.com", "a@b.com", "hi", steal=True)
    assert claimed is False
    assert "reading it now" in why


def test_steal_can_take_a_genuinely_stale_working_claim(db_session):
    make_tenant(db_session)
    _seed_working_claim(db_session, "msg-stale@example.com",
                        minutes_old=CLAIM_STALE_MINUTES + 5)

    claimed, why = _claim(db_session, "msg-stale@example.com", "a@b.com", "hi", steal=True)
    assert claimed is True


def test_a_non_steal_claim_cannot_take_an_in_flight_claim_but_can_reclaim_a_stale_one(db_session):
    """Unchanged behaviour: without steal, a claim actively being worked is off limits, but
    the normal poll's own self-healing (a crashed run's stale claim gets picked back up by
    the NEXT ordinary poll, not only by reexamine) still works exactly as before."""
    make_tenant(db_session)
    _seed_working_claim(db_session, "msg-nostealfresh@example.com", minutes_old=0)
    claimed, _ = _claim(db_session, "msg-nostealfresh@example.com", "a@b.com", "hi", steal=False)
    assert claimed is False

    _seed_working_claim(db_session, "msg-nostealstale@example.com",
                        minutes_old=CLAIM_STALE_MINUTES + 5)
    claimed2, _ = _claim(db_session, "msg-nostealstale@example.com", "a@b.com", "hi", steal=False)
    assert claimed2 is True


def test_steal_still_reopens_a_finished_verdict(db_session):
    """The one thing steal IS for - unchanged."""
    make_tenant(db_session)
    row = EmailSeen(message_id="msg-done@example.com", sender="a@b.com", subject="hi",
                    verdict="no_customer", note="nobody matched")
    db_session.add(row)
    db_session.commit()

    claimed, _ = _claim(db_session, "msg-done@example.com", "a@b.com", "hi", steal=True)
    assert claimed is True

    claimed_no_steal, why = _claim(db_session, "msg-done2@example.com", "a@b.com", "hi", steal=False)
    assert claimed_no_steal is True  # sanity: a brand new message id claims fine either way


# ---- _close_any_pending_email_for ----------------------------------------------------------

def test_a_later_success_resolves_an_earlier_pending_row_for_the_same_message(db_session):
    tenant = make_tenant(db_session)
    row = PendingEmail(message_id="msg-later-match@example.com", sender="a@b.com",
                       subject="hi", reason="no_customer", status="pending")
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    _close_any_pending_email_for(db_session, "msg-later-match@example.com", tenant.id, "job-123")
    db_session.commit()
    db_session.refresh(row)

    assert row.status == "resolved"
    assert row.resolved_group_id == tenant.id
    assert row.resolved_job_id == "job-123"


def test_no_pending_row_for_the_message_is_a_silent_no_op(db_session):
    make_tenant(db_session)
    # Must not raise just because nothing is there to close.
    _close_any_pending_email_for(db_session, "msg-nothing-pending@example.com", "g1", "j1")


def test_an_already_resolved_pending_row_is_left_alone(db_session):
    tenant = make_tenant(db_session)
    row = PendingEmail(message_id="msg-already-done@example.com", sender="a@b.com",
                       subject="hi", reason="no_customer", status="resolved",
                       resolved_group_id="original-group", resolved_job_id="original-job")
    db_session.add(row)
    db_session.commit()

    _close_any_pending_email_for(db_session, "msg-already-done@example.com", "new-group", "new-job")
    db_session.commit()
    db_session.refresh(row)

    assert row.resolved_group_id == "original-group"
    assert row.resolved_job_id == "original-job"
