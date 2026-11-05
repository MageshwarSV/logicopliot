"""ERP Submitted and ERP Verified are ordered by when the job went to the ERP.

Both screens already print that moment under each row's badge - "AI - ERP Submitted /
Nov 04, 11:34 AM". Ordering the list by created_at put those stamps in no order at all going
down the page: Nov 03 5:44pm, then 4:32pm, then 6:26pm, then Nov 04 11:07am. An operator
checking what had just gone through had to read every row to find it.

Arrival order is still right for every OTHER screen - New Jobs is about what came in.
"""
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _job(db_session, tenant, group, *, ref, created, completed, verified=None):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference=ref, status="completed",
        ai_submitted_at=created, completed_at=completed, erp_verified_at=verified,
    )
    db_session.add(job)
    db_session.flush()
    # created_at has a server default, so it is set explicitly after the insert.
    job.created_at = created
    db_session.commit()
    return job


def _setup(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-order@example.com")
    login(client, "sa-order@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()
    return tenant, group


def _refs(resp):
    return [j["reference"] for j in resp.json()]


def test_erp_submitted_lists_the_most_recently_submitted_first(client, db_session):
    tenant, group = _setup(client, db_session)
    base = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    # Deliberately the opposite order from arrival: the job that ARRIVED first was submitted
    # last. Ordering by created_at would put them exactly the wrong way round.
    _job(db_session, tenant, group, ref="FIRST-IN",
         created=base, completed=base + timedelta(hours=8))
    _job(db_session, tenant, group, ref="SECOND-IN",
         created=base + timedelta(hours=1), completed=base + timedelta(hours=2))
    _job(db_session, tenant, group, ref="THIRD-IN",
         created=base + timedelta(hours=2), completed=base + timedelta(hours=5))

    r = client.get("/api/v1/jobs?category=submitted")
    assert r.status_code == 200, r.text
    assert _refs(r) == ["FIRST-IN", "THIRD-IN", "SECOND-IN"]


def test_erp_verified_is_ordered_the_same_way(client, db_session):
    tenant, group = _setup(client, db_session)
    base = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    v = base + timedelta(days=1)
    # Arrival order is the OPPOSITE of submission order, so a list still sorted by created_at
    # answers this exactly backwards.
    _job(db_session, tenant, group, ref="NEWEST-SUBMIT",
         created=base, completed=base + timedelta(hours=9), verified=v)
    _job(db_session, tenant, group, ref="OLDEST-SUBMIT",
         created=base + timedelta(minutes=5), completed=base + timedelta(hours=1), verified=v)

    r = client.get("/api/v1/jobs?category=completed")
    assert _refs(r) == ["NEWEST-SUBMIT", "OLDEST-SUBMIT"]


def test_a_job_finished_before_the_column_existed_still_sorts_by_arrival(client, db_session):
    # completed_at is NULL on jobs that finished before it was added and whose history was too
    # old to backfill from. Those must keep their old place rather than sinking to the bottom.
    tenant, group = _setup(client, db_session)
    base = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    _job(db_session, tenant, group, ref="NO-COMPLETED-AT",
         created=base + timedelta(hours=6), completed=None)
    # Arrived LATEST but was submitted earliest, so created_at order would wrongly float it to
    # the top and the coalesce is the only thing that puts it second.
    _job(db_session, tenant, group, ref="HAS-COMPLETED-AT",
         created=base + timedelta(hours=8), completed=base + timedelta(hours=3))

    r = client.get("/api/v1/jobs?category=submitted")
    # 6h (its arrival, standing in for a submission time) beats 3h.
    assert _refs(r) == ["NO-COMPLETED-AT", "HAS-COMPLETED-AT"]


def test_new_jobs_is_still_ordered_by_arrival(client, db_session):
    # New Jobs answers "what came in", so arrival order is the right order there and must not
    # change just because the finished screens did.
    tenant, group = _setup(client, db_session)
    base = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    for ref, created in (("ARRIVED-EARLY", base),
                         ("ARRIVED-LATE", base + timedelta(hours=4))):
        job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, status="draft")
        db_session.add(job)
        db_session.flush()
        job.created_at = created
    db_session.commit()

    r = client.get("/api/v1/jobs?category=new")
    assert _refs(r) == ["ARRIVED-LATE", "ARRIVED-EARLY"]


def test_todays_erp_submitted_count_includes_a_job_that_arrived_yesterday(client, db_session):
    """ERP Submitted and ERP Verified are windowed on WHEN THEY WENT TO THE ERP.

    A job uploaded yesterday and submitted this afternoon belongs in today's ERP Submitted -
    it is exactly the row an operator opens that screen to find. Counted on arrival, it was
    missing from today's number while the list beneath it (filtered on the submission stamp)
    showed it, so the sidebar and the rows disagreed.

    New Jobs is untouched: that screen answers "what came in", so arrival is its date.
    """
    from datetime import date

    tenant, group = _setup(client, db_session)
    today = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    _job(db_session, tenant, group, ref="ARRIVED-YESTERDAY-SENT-TODAY",
         created=yesterday, completed=today)

    day = date.today().isoformat()
    r = client.get(f"/api/v1/jobs/category-counts?date_from={day}&date_to={day}")
    assert r.status_code == 200, r.text
    assert r.json()["submitted"] == 1


def test_a_job_submitted_yesterday_is_not_in_todays_count(client, db_session):
    """The other half - the window still has to exclude things."""
    from datetime import date

    tenant, group = _setup(client, db_session)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    _job(db_session, tenant, group, ref="SENT-YESTERDAY",
         created=yesterday, completed=yesterday)

    day = date.today().isoformat()
    r = client.get(f"/api/v1/jobs/category-counts?date_from={day}&date_to={day}")
    assert r.json()["submitted"] == 0
