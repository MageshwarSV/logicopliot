"""Jobs list operator_name: whoever created the job, falling back to whoever a mail pull
routed it to, and None when neither is known."""

from app.api.v1.jobs import _operator_name
from app.models.job import Job
from tests.conftest import make_tenant, make_user


def _job(tenant_id, **kwargs) -> Job:
    return Job(tenant_id=tenant_id, group_id="g1", reference="JOB-TEST", status="draft", **kwargs)


def test_operator_name_prefers_created_by(db_session):
    tenant = make_tenant(db_session)
    creator = make_user(db_session, role="operator", tenant=tenant, full_name="Alice Creator",
                        email="alice@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, full_name="Bob Assignee",
                      email="bob@example.com")
    job = _job(tenant.id, created_by_id=creator.id, assigned_operator_id=other.id)

    assert _operator_name(db_session, job) == "Alice Creator"


def test_operator_name_falls_back_to_assigned_operator_for_mail_pulled_jobs(db_session):
    tenant = make_tenant(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, full_name="Carol Operator")
    # A mail-pulled job has no creator - only whichever operator the mailbox routed to.
    job = _job(tenant.id, created_by_id=None, assigned_operator_id=operator.id)

    assert _operator_name(db_session, job) == "Carol Operator"


def test_operator_name_is_none_when_neither_is_set(db_session):
    tenant = make_tenant(db_session)
    job = _job(tenant.id, created_by_id=None, assigned_operator_id=None)

    assert _operator_name(db_session, job) is None


def test_operator_name_is_none_when_the_referenced_user_no_longer_exists(db_session):
    tenant = make_tenant(db_session)
    job = _job(tenant.id, created_by_id="nonexistent-id", assigned_operator_id=None)

    assert _operator_name(db_session, job) is None
