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


def _job(db, stage, status="draft"):
    from app.models.job import Job, JobDocument
    from app.models.template_group import TemplateGroup
    from tests.conftest import make_tenant

    tenant = make_tenant(db, name=f"T-{stage}")
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db.add(group)
    db.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=f"JOB-{stage or 'DONE'}"[:20],
              status=status, stage_override=stage)
    db.add(job)
    db.commit()
    from app.models.template_document import TemplateDocument

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db.add(tdoc)
    db.commit()
    db.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id, page_count=0))
    db.commit()
    return job


def test_a_half_made_job_is_removed_so_the_reread_makes_one_job(db_session):
    """SIT, 2026-11-21: the restart cut a mail off at "Document Classifying" (JOB-E351AB); the
    re-read then made JOB-DCDB5F beside it. The half-made job must go, with its billed pages."""
    from app.models.billable_page_event import BillablePageEvent
    from app.models.job import Job, JobDocument

    job = _job(db_session, "Document Classifying")
    db_session.add(BillablePageEvent(tenant_id=job.tenant_id, job_id=job.id, job_reference=job.reference,
                                     shipment_type="Air Import", page_count=3))
    row = _seen(db_session, "<cut@x>", "working")
    row.job_id = job.id
    db_session.commit()
    assert ep.release_interrupted_claims(db_session) == ["FW: Tomorrow Landing - DHL AWB 7112625343"]
    assert db_session.get(Job, job.id) is None
    assert db_session.query(JobDocument).filter(JobDocument.job_id == job.id).count() == 0
    assert db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).count() == 0
    db_session.refresh(row)
    assert row.verdict == ep.FAILED_VERDICT and row.job_id is None


def test_a_job_that_finished_before_the_restart_is_not_read_again(db_session):
    from app.models.job import Job

    job = _job(db_session, None, status="extracting")
    row = _seen(db_session, "<late@x>", "working", subject="cut at the very end")
    row.job_id = job.id
    db_session.commit()
    assert ep.release_interrupted_claims(db_session) == []
    assert db_session.get(Job, job.id) is not None
    db_session.refresh(row)
    assert row.verdict == "matched" and row.job_id == job.id


def test_a_job_cut_off_before_its_stage_was_set_is_still_removed(db_session):
    """SIT, 2026-11-21 (JOB-6F1B41): cut off mid-read while stage_override was still None - it
    was wrongly taken as finished. Still in draft = not past reading = removed and re-read."""
    from app.models.job import Job

    job = _job(db_session, None)
    row = _seen(db_session, "<early@x>", "working", subject="cut early")
    row.job_id = job.id
    db_session.commit()
    assert ep.release_interrupted_claims(db_session) == ["cut early"]
    assert db_session.get(Job, job.id) is None
    db_session.refresh(row)
    assert row.verdict == ep.FAILED_VERDICT
