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
