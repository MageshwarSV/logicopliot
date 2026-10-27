"""Job.completed_at - the date a job FINISHED, as opposed to the date it was created.

The Jobs list used created_at for its Date column, and the Manager app counts completed jobs
by day. Those are different dates far more often than they sound: of the first four completed
jobs in production, two finished on a later day than they were created.

The column is filled by the before_flush listener in app/db/job_history.py rather than at the
thirteen places that assign Job.status, for the same reason the history rows themselves are -
anything that has to be remembered at thirteen call sites gets forgotten at the fourteenth.
These tests are what keeps that listener honest.
"""
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.models.job_event import JobEvent
from tests.conftest import make_tenant


def _group(db_session, tenant):
    """A template group is only needed because Job.group_id is NOT NULL."""
    from app.models.template_group import TemplateGroup

    group = TemplateGroup(tenant_id=tenant.id, name="grp", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _job(db_session, tenant, group, status="draft", reference="JOB-1"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_job_that_has_not_finished_has_no_completed_at(client, db_session):
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))
    assert job.completed_at is None

    job.status = "extracted"
    db_session.commit()
    db_session.refresh(job)
    assert job.completed_at is None


def test_completing_a_job_stamps_completed_at(client, db_session):
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))

    before = datetime.now(timezone.utc)
    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)
    after = datetime.now(timezone.utc)

    assert job.completed_at is not None
    stamped = job.completed_at
    if stamped.tzinfo is None:  # SQLite hands back naive datetimes
        stamped = stamped.replace(tzinfo=timezone.utc)
    assert before <= stamped <= after


def test_completed_at_is_independent_of_created_at(client, db_session):
    """The whole point. A job created days before it finished must report the day it
    finished, not the day it arrived."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))

    job.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.commit()

    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)

    created = job.created_at
    finished = job.completed_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    assert finished > created
    assert finished.date() != created.date()


def test_re_completing_a_job_moves_the_date_forward(client, db_session):
    """Three of the first four completed jobs in production had bounced back for a retry and
    completed more than once. A job belongs to the day it FINALLY finished - keeping the first
    date would report it on a completion that was later undone, and would let one job be
    counted on two different days."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))

    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)
    first = job.completed_at

    # Bounced back for a retry, then finished again.
    job.status = "extracted"
    db_session.commit()
    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)

    assert job.completed_at >= first
    # And it matches the LAST completed event, which is what the migration backfills from.
    last_event = (
        db_session.query(JobEvent)
        .filter(JobEvent.job_id == job.id, JobEvent.status == "completed")
        .order_by(JobEvent.created_at.desc())
        .first()
    )
    assert last_event is not None
    assert abs((job.completed_at - last_event.created_at).total_seconds()) < 2


def test_reopening_a_completed_job_keeps_the_old_date_but_it_stops_counting(client, db_session):
    """Deliberate: the listener does not clear the date when a job leaves "completed". The
    stale date is harmless because such a job no longer matches the completed filter, and if
    it finishes again the date is overwritten with the real one."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))

    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)
    assert job.completed_at is not None

    job.status = "failed"
    db_session.commit()
    db_session.refresh(job)
    assert job.completed_at is not None
    assert job.status == "failed"


def test_a_job_created_already_completed_still_gets_a_date(client, db_session):
    """The dirty-object path only sees a status that CHANGED, so a job born finished would
    otherwise never be stamped at all."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant), status="completed", reference="JOB-2")
    assert job.completed_at is not None


def test_completed_at_is_exposed_on_the_jobs_api(client, db_session):
    """The Jobs list renders this under the status badge, and the Manager app reads it. If it
    stops being serialised, both silently fall back to showing nothing."""
    from app.schemas.jobs import JobOut

    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, _group(db_session, tenant))
    job.status = "completed"
    db_session.commit()
    db_session.refresh(job)

    out = JobOut.model_validate(job)
    assert out.completed_at is not None
    assert out.completed_at == job.completed_at
