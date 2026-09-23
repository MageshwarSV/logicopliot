"""GET /jobs — limit/offset pagination, and group/bucket server-side filtering matching the
operator dashboard's Job Pending / ETA / Pending Approval boxes."""
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, ref, *, created_days_ago=0, status="extracted",
             eta_date=None, gk2_status=None, created_by_id=None):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, status=status,
             created_by_id=created_by_id, assigned_operator_id=created_by_id,
             eta_date=eta_date, gk2_status=gk2_status)
    db_session.add(job)
    db_session.flush()
    job.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    db_session.add(job)
    db_session.commit()
    return job


def test_limit_and_offset_page_through_results(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-page1@example.com")
    for i in range(5):
        _make_job(db_session, tenant, group, f"JOB-{i}", created_days_ago=i)
    login(client, admin.email)

    page1 = client.get("/api/v1/jobs", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/jobs", params={"limit": 2, "offset": 2}).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})


def test_no_limit_returns_everything(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-page2@example.com")
    for i in range(5):
        _make_job(db_session, tenant, group, f"JOB-B-{i}", created_days_ago=i)
    login(client, admin.email)

    resp = client.get("/api/v1/jobs").json()
    assert len(resp) == 5


def test_group_pending_bucket_today_excludes_completed_and_old_jobs(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter1@example.com")
    today_job = _make_job(db_session, tenant, group, "JOB-TODAY", created_days_ago=0)
    _make_job(db_session, tenant, group, "JOB-OLD", created_days_ago=40)
    _make_job(db_session, tenant, group, "JOB-DONE", created_days_ago=0, status="completed")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "today"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {today_job.id}


def test_group_pending_bucket_all_includes_everything_not_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter2@example.com")
    a = _make_job(db_session, tenant, group, "JOB-A", created_days_ago=0)
    b = _make_job(db_session, tenant, group, "JOB-B", created_days_ago=90)
    _make_job(db_session, tenant, group, "JOB-C", created_days_ago=0, status="completed")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "all"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {a.id, b.id}


def test_group_pending_excludes_jobs_already_handed_to_gk2(client, db_session):
    """A job GK1 already submitted for GK2 approval is no longer "pending" in GK1's own
    queue, even though its raw status is still "extracted" - without this it was counted in
    BOTH the Job Pending box and the Pending Approval box at once."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter-gk2-1@example.com")
    still_pending = _make_job(db_session, tenant, group, "JOB-STILLPENDING", created_days_ago=0)
    _make_job(db_session, tenant, group, "JOB-WITHGK2", created_days_ago=0, gk2_status="pending")
    _make_job(db_session, tenant, group, "JOB-PREPARING", created_days_ago=0, gk2_status="preparing_erp")
    _make_job(db_session, tenant, group, "JOB-SUBMITTED", created_days_ago=0, gk2_status="submitted")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "all"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {still_pending.id}


def test_group_eta_filters_by_eta_date_not_created_at(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter3@example.com")
    today = datetime.now(timezone.utc).date()
    with_eta = _make_job(db_session, tenant, group, "JOB-ETA", created_days_ago=60,
                         eta_date=today.isoformat())
    _make_job(db_session, tenant, group, "JOB-NOETA", created_days_ago=0)
    login(client, admin.email)

    resp = client.get("/api/v1/jobs", params={"group": "eta", "bucket": "today"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {with_eta.id}


def test_group_approval_matches_gk2_pending(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter4@example.com")
    pending_approval = _make_job(db_session, tenant, group, "JOB-PENDINGGK2",
                                 created_days_ago=0, gk2_status="pending")
    _make_job(db_session, tenant, group, "JOB-NOTPENDING", created_days_ago=0)
    login(client, admin.email)

    resp = client.get("/api/v1/jobs", params={"group": "approval", "bucket": "all"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {pending_approval.id}


def test_group_filter_composes_with_pagination(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-filter5@example.com")
    for i in range(5):
        _make_job(db_session, tenant, group, f"JOB-P-{i}", created_days_ago=0)
    login(client, admin.email)

    page1 = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "all", "limit": 2, "offset": 0}).json()
    page2 = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "all", "limit": 2, "offset": 2}).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})


def test_group_filter_composes_with_operator_own_job_scope(client, db_session):
    """An operator's group/bucket filter still only ever shows their own visible jobs -
    the dashboard filter narrows further, it never widens access."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    owner = make_user(db_session, role="operator", tenant=tenant, email="op-filter@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, email="op-filter-other@example.com")
    mine = _make_job(db_session, tenant, group, "JOB-MINE", created_days_ago=0, created_by_id=owner.id)
    _make_job(db_session, tenant, group, "JOB-OTHERS", created_days_ago=0, created_by_id=other.id)
    login(client, owner.email)

    resp = client.get("/api/v1/jobs", params={"group": "pending", "bucket": "today"}).json()
    ids = {j["id"] for j in resp}
    assert ids == {mine.id}
