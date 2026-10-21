"""The dashboard's "Today"/"This Week"/"This Month" boxes (and the Jobs list they navigate to)
used to disagree for any job created between midnight and 5:30 AM IST: the box is computed in
the browser's own local time (IST, for every real user), the list re-queries the server which
reckoned "today" in UTC - so a job created at, say, 2 AM IST (20:30 UTC the PREVIOUS day) was
already "today" to the box but still "yesterday" to the list. Reported live, in production and
UAT both, as "the box shows a count but opening it shows fewer/nothing".

Fixed by reckoning _bucket_bounds in IST explicitly (see the IST constant in jobs.py) instead
of the server's own UTC clock - this is the server-side half; JobsOverview.tsx carries the
identical fix on the frontend."""
from datetime import datetime, timezone

from app.api.v1.jobs import IST, _bucket_bounds
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_a_job_created_at_2am_ist_falls_in_todays_bucket_even_though_its_still_yesterday_in_utc():
    # 2026-03-10 02:00 IST = 2026-03-09 20:30 UTC - the exact kind of moment the bug mishandled.
    created_at = datetime(2026, 3, 9, 20, 30, tzinfo=timezone.utc)
    now = datetime(2026, 3, 10, 10, 0, tzinfo=IST)  # later the same IST calendar day

    start, end = _bucket_bounds("today", now=now)
    assert start <= created_at < end

    # The OLD (UTC-reckoned) bounds would have excluded it - confirms this is a real behaviour
    # change, not a no-op.
    old_start = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
    assert not (old_start <= created_at)


def test_a_job_created_just_before_ist_midnight_is_not_counted_as_today():
    # 2026-03-09 23:59 IST = 2026-03-09 18:29 UTC - the previous IST day, genuinely.
    created_at = datetime(2026, 3, 9, 18, 29, tzinfo=timezone.utc)
    now = datetime(2026, 3, 10, 10, 0, tzinfo=IST)

    start, end = _bucket_bounds("today", now=now)
    assert not (start <= created_at < end)


def test_the_week_bucket_starts_on_ist_monday_not_utc_monday():
    # 2026-03-09 is a Monday. 00:30 IST that Monday is 2026-03-08 19:00 UTC - still Sunday
    # by the clock, but already Monday in IST.
    monday_early_ist = datetime(2026, 3, 8, 19, 0, tzinfo=timezone.utc)
    now = datetime(2026, 3, 11, 10, 0, tzinfo=IST)  # the Wednesday of the same IST week

    start, end = _bucket_bounds("week", now=now)
    assert start <= monday_early_ist < end


def test_a_real_job_row_created_in_the_early_ist_morning_is_found_by_the_fixed_bounds(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    created_at = datetime(2026, 3, 9, 20, 30, tzinfo=timezone.utc)  # 2 AM IST, 2026-03-10
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-IST1", status="extracted",
              created_at=created_at)
    db_session.add(job)
    db_session.commit()

    now = datetime(2026, 3, 10, 10, 0, tzinfo=IST)
    start, end = _bucket_bounds("today", now=now)
    # Mirrors _apply_dashboard_filter's own UTC conversion - SQLite (this test's own DB) has no
    # real timestamptz comparison the way production Postgres does, so an un-converted
    # IST-offset bound would silently mismatch here even though the real app code is correct.
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    found = (
        db_session.query(Job)
        .filter(Job.id == job.id, Job.created_at >= start, Job.created_at < end)
        .first()
    )
    assert found is not None
