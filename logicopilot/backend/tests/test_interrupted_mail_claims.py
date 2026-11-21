"""A mail cut off mid-read by a restart is read again - whatever day it arrived.

release_interrupted_claims (run once at startup, before the first poll) turns every leftover
"being read" claim into a held mail, which the poll fetches again by its own Message-ID and
_claim lets through. Finished verdicts are never touched."""
from app.core import email_puller as ep
from app.models.email_seen import EmailSeen


def _seen(db, mid, verdict, subject="FW: Tomorrow Landing - DHL AWB 7112625343"):
    row = EmailSeen(message_id=mid, sender="ops@example.com", subject=subject, verdict=verdict,
                    note="being read now" if verdict == "working" else None)
    db.add(row)
    db.commit()
    return row


def test_leftover_reading_claims_are_handed_back(db_session):
    _seen(db_session, "<cut@x>", "working")
    _seen(db_session, "<done@x>", "matched", subject="done")
    _seen(db_session, "<held@x>", ep.FAILED_VERDICT, subject="held")
    released = ep.release_interrupted_claims(db_session)
    assert released == ["FW: Tomorrow Landing - DHL AWB 7112625343"]
    rows = {r.message_id: r for r in db_session.query(EmailSeen).all()}
    assert rows["<cut@x>"].verdict == ep.FAILED_VERDICT
    assert rows["<cut@x>"].note == ep.INTERRUPTED_NOTE
    assert rows["<done@x>"].verdict == "matched"      # a finished mail is never reopened
    assert rows["<held@x>"].verdict == ep.FAILED_VERDICT


def test_a_handed_back_mail_is_fetched_by_id_and_can_be_read(db_session):
    _seen(db_session, "<cut@x>", "working")
    # before: a fresh claim is refused - "another run is reading it now"
    claimed, why = ep._claim(db_session, "<cut@x>", "ops@example.com", "s")
    assert not claimed and "reading" in why
    ep.release_interrupted_claims(db_session)
    # the poll looks it up by Message-ID, whatever its date...
    assert "<cut@x>" in [r.message_id for r in ep.failed_emails(db_session)]
    # ...and is allowed to read it
    claimed, _ = ep._claim(db_session, "<cut@x>", "ops@example.com", "s")
    assert claimed


def test_an_old_leftover_claim_is_not_started_now(db_session):
    """SIT had nine claims from August-October: reading them now would turn months-old mail
    into new jobs. Only a recent interruption is handed back."""
    from datetime import datetime, timedelta, timezone

    old = _seen(db_session, "<old@x>", "working", subject="August mail")
    old.updated_at = datetime.now(timezone.utc) - timedelta(days=40)
    db_session.commit()
    _seen(db_session, "<recent@x>", "working", subject="today's mail")
    assert ep.release_interrupted_claims(db_session) == ["today's mail"]
    db_session.refresh(old)
    assert old.verdict == "working"


def test_nothing_to_release(db_session):
    _seen(db_session, "<done@x>", "matched")
    assert ep.release_interrupted_claims(db_session) == []
