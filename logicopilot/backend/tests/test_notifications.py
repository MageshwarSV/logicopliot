"""The bell tells you two things: a job reached the ERP, or it failed to.

Nothing is stored as a notification - both kinds are derived from the jobs the user can
already see, using the same conditions the Jobs list sidebar counts with. These tests pin the
behaviour that matters: the right two kinds appear, a job nobody may see never appears, and
clearing the bell clears it for ONE person rather than for the whole tenant.
"""
from datetime import datetime, timedelta, timezone

from app.models.job import Job
from app.models.notification_read import NotificationRead
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-n@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-n@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, email="op2-n@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    return tenant, op, other, group


def _job(db_session, tenant, group, op, ref, **kw):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref,
              created_by_id=op.id, assigned_operator_id=op.id, **kw)
    db_session.add(job)
    db_session.commit()
    return job


def _get(client):
    r = client.get("/api/v1/notifications")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_submitted_and_a_failed_job_both_appear(client, db_session):
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-SENT", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    _job(db_session, tenant, group, op, "JOB-BAD", status="failed",
         erp_status="error", erp_reason="The ERP rejected the invoice number")
    login(client, op.email)

    items = _get(client)
    by_ref = {n["job_reference"]: n for n in items}
    assert by_ref["JOB-SENT"]["kind"] == "submitted"
    assert by_ref["JOB-BAD"]["kind"] == "failed"
    # The first question about a failure is always why.
    assert by_ref["JOB-BAD"]["detail"] == "The ERP rejected the invoice number"
    assert all(n["read"] is False for n in items)


def test_an_ordinary_job_in_progress_is_not_a_notification(client, db_session):
    """Only the two ERP outcomes. A job being worked on is on the screen already."""
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-WIP", status="extracted")
    login(client, op.email)
    assert _get(client) == []


def test_clicking_one_marks_only_that_one_read(client, db_session):
    tenant, op, other, group = _setup(db_session)
    a = _job(db_session, tenant, group, op, "JOB-A", status="completed",
             ai_submitted_at=datetime.now(timezone.utc))
    _job(db_session, tenant, group, op, "JOB-B", status="failed", erp_status="error")
    login(client, op.email)

    r = client.post("/api/v1/notifications/read", json={"ids": [f"submitted:{a.id}"]})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1

    by_ref = {n["job_reference"]: n for n in _get(client)}
    assert by_ref["JOB-A"]["read"] is True
    assert by_ref["JOB-B"]["read"] is False


def test_mark_all_as_read_clears_the_bell(client, db_session):
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    _job(db_session, tenant, group, op, "JOB-B", status="failed", erp_status="error")
    login(client, op.email)

    r = client.post("/api/v1/notifications/read", json={})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 2
    assert all(n["read"] is True for n in _get(client))


def test_the_notifications_stay_in_the_list_after_being_read(client, db_session):
    """Read, not deleted - so "View all" still shows what happened, and a second click on a
    job you already opened still takes you there."""
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    login(client, op.email)

    client.post("/api/v1/notifications/read", json={})
    items = _get(client)
    assert len(items) == 1
    assert items[0]["read"] is True
    assert items[0]["job_id"]           # still clickable


def test_marking_read_is_per_person(client, db_session):
    """A Gate Keeper clearing their own bell must not clear everyone else's."""
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))

    login(client, op.email)
    client.post("/api/v1/notifications/read", json={})
    assert all(n["read"] for n in _get(client))

    login(client, other.email)
    assert all(not n["read"] for n in _get(client))


def test_reading_twice_does_not_pile_up_rows(client, db_session):
    tenant, op, other, group = _setup(db_session)
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    login(client, op.email)

    assert client.post("/api/v1/notifications/read", json={}).json()["marked"] == 1
    assert client.post("/api/v1/notifications/read", json={}).json()["marked"] == 0
    assert db_session.query(NotificationRead).count() == 1


def test_a_failure_and_a_later_success_are_two_separate_notifications(client, db_session):
    """Reading the failure must not silence the success that follows it - which is why kind is
    part of the read key rather than just the job."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "JOB-RETRY", status="failed", erp_status="error")
    login(client, op.email)
    client.post("/api/v1/notifications/read", json={})

    job.status = "completed"
    job.ai_submitted_at = datetime.now(timezone.utc)
    db_session.commit()

    items = _get(client)
    assert [n["kind"] for n in items] == ["submitted"]
    assert items[0]["read"] is False, "the success was silenced by having read the failure"


def test_something_old_is_not_a_notification(client, db_session):
    """A six-week-old event is a report, not a notification - and the Jobs list is already the
    report. This is also what keeps "mark all as read" bounded."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "JOB-OLD", status="completed",
               ai_submitted_at=datetime.now(timezone.utc) - timedelta(days=90))
    job.updated_at = datetime.now(timezone.utc) - timedelta(days=90)
    db_session.commit()
    login(client, op.email)
    assert _get(client) == []


def test_another_tenants_job_never_appears(client, db_session):
    tenant, op, other, group = _setup(db_session)
    stranger_tenant = make_tenant(db_session, name="Someone Else")
    make_user(db_session, role=TENANT_ADMIN, tenant=stranger_tenant, email="ta-x@example.com")
    stranger = make_user(db_session, role="operator", tenant=stranger_tenant,
                         email="op-x@example.com")
    g2 = TemplateGroup(tenant_id=stranger_tenant.id, name="Theirs", mode="Sea Import")
    db_session.add(g2)
    db_session.commit()
    _job(db_session, stranger_tenant, g2, stranger, "JOB-THEIRS", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))

    login(client, op.email)
    assert _get(client) == []


def test_a_nonsense_id_is_rejected(client, db_session):
    tenant, op, other, group = _setup(db_session)
    login(client, op.email)
    r = client.post("/api/v1/notifications/read", json={"ids": ["not-a-kind:abc"]})
    assert r.status_code == 400
