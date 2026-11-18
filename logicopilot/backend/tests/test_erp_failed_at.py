"""The Jobs list shows WHEN a job's ERP entry failed, under its "Failed" badge - the way it
shows when it was submitted under "ERP Submitted". Job.erp_failed_at is stamped by a listener
on gk2_status, so every path that fails a job sets it."""
import time
import uuid

from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.schemas.jobs import JobOut
from tests.conftest import make_tenant


def _job(db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved", mode="Air Import")
    db_session.add(group)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=f"JOB-{uuid.uuid4().hex[:6]}", status="extracted")
    db_session.add(job)
    db_session.commit()
    return job


def test_failing_a_job_records_when(db_session):
    job = _job(db_session)
    assert job.erp_failed_at is None
    job.gk2_status = "entering_erp"
    db_session.commit()
    assert job.erp_failed_at is None
    job.gk2_status = "failed"
    db_session.commit()
    assert job.erp_failed_at is not None


def test_a_retry_keeps_the_time_and_a_second_failure_updates_it(db_session):
    job = _job(db_session)
    job.gk2_status = "failed"
    db_session.commit()
    first = job.erp_failed_at
    job.gk2_status = "entering_erp"           # retried
    db_session.commit()
    assert job.erp_failed_at == first          # still says when it last failed
    time.sleep(0.01)
    job.gk2_status = "failed"                  # failed again
    db_session.commit()
    assert job.erp_failed_at > first


def test_setting_failed_again_while_already_failed_does_not_move_it(db_session):
    job = _job(db_session)
    job.gk2_status = "failed"
    db_session.commit()
    first = job.erp_failed_at
    job.gk2_status = "failed"
    db_session.commit()
    assert job.erp_failed_at == first


def test_the_job_list_row_carries_it(db_session):
    job = _job(db_session)
    job.gk2_status = "failed"
    db_session.commit()
    assert JobOut.model_validate(job).erp_failed_at == job.erp_failed_at
