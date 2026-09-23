"""_job_stage: a job must always pass through Data Validation (Ready for Review) at least
once before it can reach ERP Submission - even a template with NO cross-document checks
configured, which used to skip straight there with nobody ever having looked at the data.
See the `validation_approved` column comment on the Job model - this is exactly the gap it
was meant to guard against, that the stage computation was bypassing entirely."""

from app.api.v1.jobs import _job_stage
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_group(db_session, tenant, **kw):
    group = TemplateGroup(tenant_id=tenant.id, name="No Cross-Check Template", status="approved", **kw)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, status="extracted"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TEST", status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_template_with_no_cross_checks_still_requires_review_before_erp_submission(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)  # no CrossDocLink rows at all
    job = _make_job(db_session, tenant, group)
    assert _job_stage(db_session, job) == "Data Validation"


def test_after_the_operator_records_moving_past_review_it_proceeds_normally(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status=job.status,
                            stage="ERP Submission", note="moved on"))
    db_session.commit()
    assert _job_stage(db_session, job) == "ERP Submission"


def test_a_recorded_intermediate_stage_is_honoured_as_before(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status=job.status,
                            stage="Dump Data", note="moved on"))
    db_session.commit()
    assert _job_stage(db_session, job) == "Dump Data"


def test_a_job_not_yet_extracted_is_unaffected_by_the_review_gate(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="draft")
    assert _job_stage(db_session, job) == "Document Capture"


def test_a_completed_job_is_unaffected_by_the_review_gate(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="completed")
    assert _job_stage(db_session, job) == "Completed"


def test_a_manual_stage_override_still_wins_over_the_review_gate(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    job.stage_override = "Manual Data Entry"
    db_session.commit()
    assert _job_stage(db_session, job) == "Manual Data Entry"
