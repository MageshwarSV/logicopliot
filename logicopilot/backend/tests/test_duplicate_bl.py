"""Recognising a pre-alert that announces a BL this customer already sent us.

The rule is narrow on purpose: the SAME normalised BL, from the SAME sender, inside the
window, on a message that actually became a job. Each of those four matters, and each has a
test here - a check that fires too easily silently drops a real shipment, which is far worse
than the duplicate job it was trying to prevent.
"""
from datetime import datetime, timedelta, timezone

from app.core.duplicate_bl import DUPLICATE_WINDOW_DAYS, already_queued, find_original
from app.models.duplicate_email import DuplicateEmail
from app.models.email_seen import EmailSeen
from tests.conftest import make_tenant

NOW = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


def _seen(db, *, bl, sender, job_id="job-1", days_ago=1, verdict="matched"):
    row = EmailSeen(message_id=f"<{bl}-{sender}-{days_ago}-{job_id}@x>", sender=sender,
                    subject=f"BL NO: {bl}", verdict=verdict, bl_number=bl, job_id=job_id)
    db.add(row)
    db.flush()
    # created_at is server-side; set it explicitly so the window can actually be tested.
    row.created_at = NOW - timedelta(days=days_ago)
    db.commit()
    return row


def test_the_same_bl_from_the_same_sender_is_a_duplicate(db_session):
    _seen(db_session, bl="1078317130", sender="ops@4slogistics.com", days_ago=3)
    got = find_original(db_session, bl_number="1078317130",
                        sender="ops@4slogistics.com", now=NOW)
    assert got is not None
    assert got.job_id == "job-1"


def test_a_different_sender_is_not_a_duplicate(db_session):
    """Two customers can legitimately quote the same carrier reference. The comparison is
    per account, which is what the requirement asks for."""
    _seen(db_session, bl="1078317130", sender="ops@4slogistics.com", days_ago=3)
    assert find_original(db_session, bl_number="1078317130",
                         sender="someone@else.com", now=NOW) is None


def test_a_different_bl_is_not_a_duplicate(db_session):
    _seen(db_session, bl="1078317130", sender="ops@4slogistics.com", days_ago=3)
    assert find_original(db_session, bl_number="1078317131",
                         sender="ops@4slogistics.com", now=NOW) is None


def test_older_than_the_window_is_not_a_duplicate(db_session):
    """A shipment genuinely repeating months later is a new job, not a re-send."""
    _seen(db_session, bl="1078317130", sender="ops@4slogistics.com",
          days_ago=DUPLICATE_WINDOW_DAYS + 2)
    assert find_original(db_session, bl_number="1078317130",
                         sender="ops@4slogistics.com", now=NOW) is None


def test_just_inside_the_window_still_counts(db_session):
    _seen(db_session, bl="1078317130", sender="ops@4slogistics.com",
          days_ago=DUPLICATE_WINDOW_DAYS - 1)
    assert find_original(db_session, bl_number="1078317130",
                         sender="ops@4slogistics.com", now=NOW) is not None


def test_an_earlier_mail_that_never_became_a_job_is_not_something_to_duplicate(db_session):
    """It was ignored, or it failed before a job existed. There is nothing for a second copy
    to be a duplicate OF, and blocking it would lose the shipment entirely."""
    row = _seen(db_session, bl="1078317130", sender="ops@4slogistics.com", days_ago=2)
    row.job_id = None
    db_session.commit()
    assert find_original(db_session, bl_number="1078317130",
                         sender="ops@4slogistics.com", now=NOW) is None


def test_the_oldest_match_is_reported_as_the_original(db_session):
    """Three copies of one pre-alert: the FIRST one is the job the others duplicate, and is
    what the screen has to name."""
    _seen(db_session, bl="BL9", sender="ops@x.com", job_id="job-first", days_ago=9)
    _seen(db_session, bl="BL9", sender="ops@x.com", job_id="job-second", days_ago=4)
    got = find_original(db_session, bl_number="BL9", sender="ops@x.com", now=NOW)
    assert got.job_id == "job-first"


def test_nothing_to_compare_is_never_a_duplicate(db_session):
    assert find_original(db_session, bl_number="", sender="ops@x.com", now=NOW) is None
    assert find_original(db_session, bl_number="BL9", sender="", now=NOW) is None


def test_a_message_already_in_the_queue_is_recognised(db_session):
    """A mailbox re-poll must not stack up copies of the same duplicate."""
    tenant = make_tenant(db_session)
    db_session.add(DuplicateEmail(message_id="<dup-1@x>", tenant_id=tenant.id,
                                  sender="ops@x.com", subject="BL NO: BL9",
                                  bl_number="BL9", status="pending"))
    db_session.commit()
    assert already_queued(db_session, "<dup-1@x>") is True
    assert already_queued(db_session, "<never-seen@x>") is False


def test_a_handled_duplicate_is_still_recognised(db_session):
    """Deleted or processed, the row stays - so the same message arriving a third time is
    not queued all over again for someone to decide a second time."""
    tenant = make_tenant(db_session)
    db_session.add(DuplicateEmail(message_id="<dup-2@x>", tenant_id=tenant.id,
                                  sender="ops@x.com", subject="BL NO: BL9",
                                  bl_number="BL9", status="deleted"))
    db_session.commit()
    assert already_queued(db_session, "<dup-2@x>") is True
