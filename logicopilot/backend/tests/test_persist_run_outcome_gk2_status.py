"""Live bug on JOB-0D95C1: an earlier GK2 attempt had failed (job.gk2_status="failed"), then a
Super Admin re-ran the real ERP entry through the ORDINARY entry screen (not GK2) and it
succeeded - job.status correctly became "completed", but persist_run_outcome never touched
job.gk2_status, so the stale "failed" value lingered. _outer_status() checks job.gk2_status
BEFORE job.status, so the job kept showing "Failed" forever despite having actually completed.

_run_gk2_submission's own wrapper always sets job.gk2_status again right after calling
persist_run_outcome, based on the fresh outcome - so persist_run_outcome clearing it
unconditionally is safe for that caller too (its own value simply gets overwritten a moment
later, exactly as before this fix)."""
from app.api.v1.jobs import persist_run_outcome
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job(db_session, gk2_status=None):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-GK2STALE",
             status="extracted", gk2_status=gk2_status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_later_success_clears_a_stale_failed_gk2_status(db_session):
    job = _make_job(db_session, gk2_status="failed")
    persist_run_outcome(db_session, job, script=None, result={"status": "ok", "log": ["ok"]})
    assert job.status == "completed"
    assert job.gk2_status is None


def test_a_fresh_failure_clears_a_stale_submitted_gk2_status(db_session):
    job = _make_job(db_session, gk2_status="submitted")
    persist_run_outcome(db_session, job, script=None, result={
        "status": "failed", "reason": "stuck", "log": [],
    })
    assert job.status == "failed"
    assert job.gk2_status is None


def test_a_duplicate_outcome_clears_a_stale_gk2_status(db_session):
    job = _make_job(db_session, gk2_status="failed")
    persist_run_outcome(db_session, job, script=None, result={"status": "duplicated", "log": []})
    assert job.status == "duplicate"
    assert job.gk2_status is None


def test_a_partial_outcome_still_completes_and_clears_a_stale_gk2_status(db_session):
    job = _make_job(db_session, gk2_status="failed")
    persist_run_outcome(db_session, job, script=None, result={
        "status": "partial", "failed_steps": ["step 9"], "log": [],
    })
    assert job.status == "completed"
    assert job.gk2_status is None


def test_a_run_with_no_prior_gk2_status_is_unaffected(db_session):
    """The common case - a job never touched by GK2 at all - must keep behaving exactly as
    before this fix: gk2_status stays None, nothing new to clear."""
    job = _make_job(db_session, gk2_status=None)
    persist_run_outcome(db_session, job, script=None, result={"status": "ok", "log": ["ok"]})
    assert job.status == "completed"
    assert job.gk2_status is None
