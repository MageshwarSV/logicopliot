"""Two related rerun bugs, both about a job's stage/status jumping somewhere the operator
never actually reached:

1. Re-run extraction (_begin_extraction) used to leave a stale stage_override / JobEvent
   history in place, so a job already moved to ERP Submission before still read "Ready for
   Submission" after being re-extracted from scratch — even though the fresh data had not
   been reviewed at all yet.
2. "Rerun job" / "Ready for entry" (_put_back_on_entry) had no guard requiring an actual
   failed ERP run to exist, so pressing it on a job still in Data Validation skipped it
   straight past GK1 review to "Ready for Submission"."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.api.v1.jobs import _begin_extraction, _job_stage
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, *, status="extracted", created_by_id=None):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-RERUN",
             status=status, created_by_id=created_by_id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# --------------------------------------------------------- _begin_extraction stage reset


def test_begin_extraction_clears_stale_stage_override_and_verifications(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    job.stage_override = "ERP Submission"
    job.accepted_verifications = ["link-1"]
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status="extracted",
                            stage="ERP Submission", note="moved on"))
    db_session.commit()

    with patch("app.api.v1.jobs._start_extraction_background"):
        _begin_extraction(db_session, job)

    assert job.stage_override is None
    assert job.accepted_verifications is None
    assert job.status == "extracting"


def test_stage_reads_data_validation_after_reextraction_completes(db_session):
    """The real end-to-end symptom: a job previously at ERP Submission, re-extracted, must
    read Data Validation (GK1 Review) once the fresh read finishes — not fall back to the
    stale "ERP Submission" JobEvent from before."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    job.stage_override = "ERP Submission"
    stale_event = JobEvent(tenant_id=tenant.id, job_id=job.id, status="extracted",
                           stage="ERP Submission", note="moved on")
    db_session.add(stale_event)
    db_session.commit()
    # Unambiguously in the past — otherwise this and the JobEvent _begin_extraction adds
    # below can land in the same instant and make "most recent" a coin flip, not a real test.
    stale_event.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.commit()
    assert _job_stage(db_session, job) == "ERP Submission"  # sanity: reproduces the bug first

    with patch("app.api.v1.jobs._start_extraction_background"):
        _begin_extraction(db_session, job)
    # Simulate run_extraction finishing successfully (it sets status back to "extracted").
    job.status = "extracted"
    db_session.commit()

    assert _job_stage(db_session, job) == "Data Validation"


def test_begin_extraction_on_a_fresh_job_still_lands_on_data_validation(db_session):
    """No prior history at all — the ordinary first-extraction case must be unaffected."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="draft")
    db_session.commit()

    with patch("app.api.v1.jobs._start_extraction_background"):
        _begin_extraction(db_session, job)
    job.status = "extracted"
    db_session.commit()

    assert _job_stage(db_session, job) == "Data Validation"


# --------------------------------------------------------------- rerun_job / ready-for-entry


def test_rerun_job_rejected_with_no_failed_run(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rerun1@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/rerun")
    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"].lower()


def test_rerun_job_rejected_while_still_in_document_capture(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rerun2@example.com")
    job = _make_job(db_session, tenant, group, status="draft", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/rerun")
    assert resp.status_code == 409


def test_rerun_job_succeeds_on_an_actually_failed_run(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rerun3@example.com")
    job = _make_job(db_session, tenant, group, status="failed", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/rerun")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "ERP Submission"


def test_ready_for_entry_rejected_with_no_failed_run(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rerun1@example.com")
    job = _make_job(db_session, tenant, group, status="extracted")
    login(client, ta.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/ready-for-entry")
    assert resp.status_code == 409


def test_ready_for_entry_succeeds_on_an_actually_failed_run(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rerun2@example.com")
    job = _make_job(db_session, tenant, group, status="failed")
    login(client, ta.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/ready-for-entry")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == "ERP Submission"


def test_rerun_job_still_rejected_while_processing(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rerun4@example.com")
    job = _make_job(db_session, tenant, group, status="processing", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/rerun")
    assert resp.status_code == 409
    assert "running" in resp.json()["detail"].lower()


def test_rerun_job_still_rejected_when_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rerun5@example.com")
    job = _make_job(db_session, tenant, group, status="completed", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/rerun")
    assert resp.status_code == 409
    assert "twice" in resp.json()["detail"].lower()
