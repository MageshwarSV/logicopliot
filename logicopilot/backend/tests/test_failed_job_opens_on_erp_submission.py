"""A job that has just failed must open on the screen where you can retry it.

A failed ERP run deliberately leaves job.status on "extracted" so the job can be retried. The
stage was then recomputed from verification findings and the operator's own recorded progress,
which for most jobs lands on "Data Validation" - so clicking a job the list has just badged
"Failed" opened the validation tab, with no failure banner and no "Retry & Proceed" button
anywhere in sight.

Confirmed on production 2026-11-11: all 8 failed jobs had stage_override None, and 5 of them
computed to "Data Validation" or had no stage event at all. SIT appeared to work only because
its single retryable failure happened to carry an "ERP Submission" event in its history - the
same bug, masked by that one job's past.
"""
from app.api.v1.jobs import _job_stage, _outer_status
from app.models.job import Job, JobEvent
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN
from tests.conftest import make_tenant, make_user


def _job(db_session, **kw):
    # Unique per call: tenants.name is UNIQUE, and one test builds several jobs.
    tenant = make_tenant(db_session, name=f"T-{kw.get('reference')}")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{kw.get('reference')}@e.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    return job


def test_a_freshly_failed_job_opens_on_erp_submission(db_session):
    """The production case: no stage_override, no stage event at all."""
    job = _job(db_session, reference="JOB-FAILED", status="extracted", gk2_status="failed")
    assert _job_stage(db_session, job) == "ERP Submission"


def test_it_still_reads_as_Failed_everywhere_else(db_session):
    """The badge must not become "Ready for Submission" - _outer_status answers from
    gk2_status before it ever looks at the stage, and this test pins that."""
    job = _job(db_session, reference="JOB-BADGE", status="extracted", gk2_status="failed")
    assert _outer_status(db_session, job) == "Failed"
    assert _outer_status(db_session, job, stage=_job_stage(db_session, job)) == "Failed"


def test_a_failed_job_whose_history_says_Data_Validation_still_opens_on_erp_submission(db_session):
    """The exact shape of JOB-6B2F53 / JOB-29DFFA / JOB-E5CDEC on production."""
    job = _job(db_session, reference="JOB-HIST", status="extracted", gk2_status="failed")
    db_session.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id,
                            status="extracted", stage="Data Validation"))
    db_session.commit()
    assert _job_stage(db_session, job) == "ERP Submission"


def test_an_explicit_override_still_wins(db_session):
    """Retry sets stage_override itself; a deliberate statement about where a job IS must
    still outrank this fallback."""
    job = _job(db_session, reference="JOB-OVR", status="extracted", gk2_status="failed",
               stage_override="Dump Data")
    assert _job_stage(db_session, job) == "Dump Data"


def test_a_hard_failed_job_still_reads_Failed_as_its_stage(db_session):
    """job.status == "failed" is checked earlier and is a different thing - that job is not
    retryable from the ERP tab, and must keep its own word."""
    job = _job(db_session, reference="JOB-HARD", status="failed")
    assert _job_stage(db_session, job) == "Failed"


def test_a_job_that_has_not_failed_is_untouched(db_session):
    """The whole point: this changes nothing for any job that is not a failed ERP run."""
    job = _job(db_session, reference="JOB-OK", status="extracted")
    assert _job_stage(db_session, job) != "ERP Submission"

    for gk2 in ("pending", "submitted", "preparing_erp", "entering_erp"):
        j = _job(db_session, reference=f"JOB-{gk2}", status="extracted", gk2_status=gk2)
        assert _job_stage(db_session, j) != "ERP Submission", f"{gk2} was moved by this change"


def test_a_completed_job_is_untouched(db_session):
    job = _job(db_session, reference="JOB-DONE", status="completed", gk2_status="failed")
    assert _job_stage(db_session, job) == "Completed", "a finished job must keep its own word"
