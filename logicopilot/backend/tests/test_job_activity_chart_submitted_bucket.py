"""GET /jobs/activity-chart's "erp_submitted_jobs" bucket. Found live (2026-11-10): a job that
reached "completed" via the direct Submit Entry path without Job.ai_submitted_at ever being set
(a legacy shape - complete_job now always sets it, but an older job saved before that still
has it blank) was invisible to this chart in EVERY month, despite correctly showing on the
Jobs list's own "ERP Submitted Jobs" screen (_category_condition("submitted") already covers
that shape via its own fallback). Fixed to use the same _category_condition("submitted") the
sidebar/Jobs list use, bucketed by ai_submitted_at when set, falling back to completed_at."""
from datetime import datetime, timezone

from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kwargs):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference=kwargs.pop("reference", "JOB-ACT"),
        status=kwargs.pop("status", "extracted"), **kwargs,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_legacy_job_with_no_ai_submitted_at_still_counts_as_submitted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="act-op1@example.com")
    # The exact shape found live: status "completed", ai_submitted_at never set, completed_at
    # stamped on a real day this month.
    legacy = _make_job(
        db_session, tenant, group, reference="JOB-LEGACY", status="completed",
        ai_submitted_at=None, completed_at=datetime(2026, 10, 14, 18, 34, tzinfo=timezone.utc),
    )
    login(client, op.email)

    resp = client.get("/api/v1/jobs/activity-chart", params={"month": "2026-10"})
    assert resp.status_code == 200, resp.text
    days = {d["date"]: d["erp_submitted_jobs"] for d in resp.json()["days"]}
    assert sum(days.values()) == 1
    assert days[legacy.completed_at.date().isoformat()] == 1


def test_an_automated_job_is_still_bucketed_by_its_own_ai_submitted_at(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="act-op2@example.com")
    automated = _make_job(
        db_session, tenant, group, reference="JOB-AUTO", status="completed",
        ai_submitted_at=datetime(2026, 10, 15, 7, 20, tzinfo=timezone.utc),
        completed_at=datetime(2026, 10, 15, 7, 20, tzinfo=timezone.utc),
    )
    login(client, op.email)

    resp = client.get("/api/v1/jobs/activity-chart", params={"month": "2026-10"})
    assert resp.status_code == 200, resp.text
    days = {d["date"]: d["erp_submitted_jobs"] for d in resp.json()["days"]}
    assert days["2026-10-15"] == 1


def test_a_job_still_in_progress_is_not_counted_as_submitted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="act-op3@example.com")
    _make_job(db_session, tenant, group, reference="JOB-NEW", status="extracted")
    login(client, op.email)

    resp = client.get("/api/v1/jobs/activity-chart", params={"month": "2026-10"})
    assert resp.status_code == 200, resp.text
    assert sum(d["erp_submitted_jobs"] for d in resp.json()["days"]) == 0


def test_a_failed_job_is_not_counted_as_submitted_even_with_a_stale_ai_submitted_at(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="act-op4@example.com")
    _make_job(
        db_session, tenant, group, reference="JOB-FAILED", status="failed",
        ai_submitted_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    login(client, op.email)

    resp = client.get("/api/v1/jobs/activity-chart", params={"month": "2026-10"})
    assert resp.status_code == 200, resp.text
    assert sum(d["erp_submitted_jobs"] for d in resp.json()["days"]) == 0


# ---- the chart and the sidebar badge count the same jobs; Pending is a real count -----------

def _dashboard(db_session, email):
    """A tenant with an active Tenant Admin (operators cannot log in without one) and an
    operator to look at the dashboard as."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="tenant_admin", tenant=tenant, email=f"ta-{email}")
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email=email)
    return tenant, group, op


def _utc(y, m, d, h=7):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def _chart(client, month):
    r = client.get("/api/v1/jobs/activity-chart", params={"month": month})
    assert r.status_code == 200, r.text
    return r.json()


def _badge_submitted(client, date_from, date_to):
    """What the dashboard's sidebar asks for: the chart's month as a date range."""
    r = client.get("/api/v1/jobs/category-counts",
                   params={"date_from": date_from, "date_to": date_to})
    assert r.status_code == 200, r.text
    return r.json()["submitted"]


def test_a_job_retried_into_november_is_november_on_the_chart_and_the_badge(client, db_session):
    """First sent in October, failed, completed again in November. The badge and the Jobs list
    date it by that completion; the chart used the first submission and put it in October -
    the 94 vs 92 seen on the dashboard."""
    tenant, group, op = _dashboard(db_session, "act-retry@example.com")
    _make_job(db_session, tenant, group, reference="JOB-RETRIED", status="completed",
              created_at=_utc(2026, 10, 20), ai_submitted_at=_utc(2026, 10, 30),
              completed_at=_utc(2026, 11, 5))
    login(client, op.email)

    nov = _chart(client, "2026-11")
    assert {d["date"]: d["erp_submitted_jobs"] for d in nov["days"]}["2026-11-05"] == 1
    assert sum(d["erp_submitted_jobs"] for d in _chart(client, "2026-10")["days"]) == 0
    assert _badge_submitted(client, "2026-11-01", "2026-11-30") == 1


def test_the_chart_total_always_equals_the_sidebar_badge_for_the_same_month(client, db_session):
    tenant, group, op = _dashboard(db_session, "act-agree@example.com")
    shapes = [
        # retried: first submission October, completed November
        dict(status="completed", created_at=_utc(2026, 10, 20), ai_submitted_at=_utc(2026, 10, 30),
             completed_at=_utc(2026, 11, 5)),
        # arrived October, submitted November
        dict(status="completed", created_at=_utc(2026, 10, 31), ai_submitted_at=_utc(2026, 11, 2),
             completed_at=_utc(2026, 11, 2)),
        # ordinary November job
        dict(status="completed", created_at=_utc(2026, 11, 3), ai_submitted_at=_utc(2026, 11, 3),
             completed_at=_utc(2026, 11, 3)),
        # legacy: completed, never had ai_submitted_at
        dict(status="completed", created_at=_utc(2026, 11, 4), completed_at=_utc(2026, 11, 6)),
        # GK2 path: submitted but its status never became "completed"
        dict(status="extracted", gk2_status="submitted", created_at=_utc(2026, 11, 7),
             ai_submitted_at=_utc(2026, 11, 8)),
        # submitted entirely in October - in neither November number
        dict(status="completed", created_at=_utc(2026, 10, 2), ai_submitted_at=_utc(2026, 10, 3),
             completed_at=_utc(2026, 10, 3)),
    ]
    for i, kw in enumerate(shapes):
        _make_job(db_session, tenant, group, reference=f"JOB-S{i}", **kw)
    login(client, op.email)

    chart_total = sum(d["erp_submitted_jobs"] for d in _chart(client, "2026-11")["days"])
    assert chart_total == _badge_submitted(client, "2026-11-01", "2026-11-30") == 5


def test_pending_is_this_months_arrivals_that_have_not_reached_the_erp(client, db_session):
    """Arrived in November: one submitted, one still in review, one failed -> 2 pending. An
    October arrival submitted in November is not one of November's jobs at all. The old
    on-screen sum (new 3 - submitted 2 = 1) got this wrong."""
    tenant, group, op = _dashboard(db_session, "act-pending@example.com")
    _make_job(db_session, tenant, group, reference="JOB-DONE", status="completed",
              created_at=_utc(2026, 11, 3), ai_submitted_at=_utc(2026, 11, 4),
              completed_at=_utc(2026, 11, 4))
    _make_job(db_session, tenant, group, reference="JOB-REVIEW", status="extracted",
              created_at=_utc(2026, 11, 5))
    _make_job(db_session, tenant, group, reference="JOB-FAILED", status="failed",
              erp_status="error", created_at=_utc(2026, 11, 6))
    _make_job(db_session, tenant, group, reference="JOB-OCT", status="completed",
              created_at=_utc(2026, 10, 30), ai_submitted_at=_utc(2026, 11, 2),
              completed_at=_utc(2026, 11, 2))
    login(client, op.email)

    nov = _chart(client, "2026-11")
    assert sum(d["new_jobs"] for d in nov["days"]) == 3
    assert sum(d["erp_submitted_jobs"] for d in nov["days"]) == 2
    assert nov["pending_jobs"] == 2
