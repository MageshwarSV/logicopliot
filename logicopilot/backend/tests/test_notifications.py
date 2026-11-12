"""The bell tells you two things: a job reached the ERP, or it failed to.

Nothing is stored as a notification - both kinds are derived from the jobs the user can
already see, using the same conditions the Jobs list sidebar counts with, narrowed to the jobs
the user themself worked on. These tests pin the behaviour that matters: the right two kinds
appear, a job nobody may see never appears, a colleague's job never appears, and clearing the
bell clears it for ONE person rather than for the whole tenant.
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
    """A Gate Keeper clearing their own bell must not clear everyone else's. Both people worked
    on this job (op created it, other gave the GK1 approval), so it is on both bells."""
    tenant, op, other, group = _setup(db_session)
    _give_modes(db_session, op, other)      # so the job routed to op is visible to other too
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc), validation_approved_by_id=other.id)

    login(client, op.email)
    client.post("/api/v1/notifications/read", json={})
    assert all(n["read"] for n in _get(client))

    login(client, other.email)
    items = _get(client)
    assert [n["job_reference"] for n in items] == ["JOB-A"]
    assert items[0]["read"] is False


# ---- whose notification is it ------------------------------------------------------------

def _give_modes(db_session, *users, modes=("Sea Import",)):
    for u in users:
        u.assigned_modes = list(modes)
    db_session.commit()


def test_another_operators_job_in_the_same_mode_is_not_on_my_bell(client, db_session):
    """A mode-assigned operator SEES every job of that mode, but a colleague's ERP result is
    not theirs to be told about - the bell used to announce it to everyone in the mode."""
    tenant, op, other, group = _setup(db_session)
    _give_modes(db_session, op, other)
    _job(db_session, tenant, group, op, "JOB-MINE", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    _job(db_session, tenant, group, other, "JOB-THEIRS", status="failed", erp_status="error")
    login(client, op.email)

    # Still visible on the Jobs list - only the bell is narrowed.
    listed = {j["reference"] for j in client.get("/api/v1/jobs").json()}
    assert "JOB-THEIRS" in listed
    assert [n["job_reference"] for n in _get(client)] == ["JOB-MINE"]


def test_a_shared_job_i_approved_is_on_my_bell(client, db_session):
    """An email job nobody was routed to, picked up and approved by this operator, is theirs."""
    tenant, op, other, group = _setup(db_session)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SHARED",
              status="failed", erp_status="error", validation_approved_by_id=op.id)
    db_session.add(job)
    db_session.commit()

    login(client, op.email)
    assert [n["job_reference"] for n in _get(client)] == ["JOB-SHARED"]
    login(client, other.email)
    assert _get(client) == []


def test_a_gk2_is_told_only_about_the_jobs_they_approved(client, db_session):
    tenant, op, other, group = _setup(db_session)
    gk2_a = make_user(db_session, role="gk2", tenant=tenant, email="gk2a-n@example.com")
    gk2_b = make_user(db_session, role="gk2", tenant=tenant, email="gk2b-n@example.com")
    _give_modes(db_session, gk2_a, gk2_b)
    _job(db_session, tenant, group, op, "JOB-GK2", status="completed", gk2_status="submitted",
         ai_submitted_at=datetime.now(timezone.utc), gk2_validation_approved_by_id=gk2_a.id)

    login(client, gk2_a.email)
    assert [n["job_reference"] for n in _get(client)] == ["JOB-GK2"]
    login(client, gk2_b.email)
    assert _get(client) == []


def test_a_manager_and_a_tenant_admin_see_every_erp_result_in_their_tenant(client, db_session):
    """Oversight roles, by explicit request: watching the whole tenant's ERP outcomes is their
    job, so they are told about every operator's - but still never another tenant's."""
    tenant, op, other, group = _setup(db_session)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-n@example.com")
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    _job(db_session, tenant, group, other, "JOB-B", status="failed", erp_status="error")

    stranger_tenant = make_tenant(db_session, name="Someone Else")
    make_user(db_session, role=TENANT_ADMIN, tenant=stranger_tenant, email="ta-x@example.com")
    stranger = make_user(db_session, role="operator", tenant=stranger_tenant,
                         email="op-x@example.com")
    g2 = TemplateGroup(tenant_id=stranger_tenant.id, name="Theirs", mode="Sea Import")
    db_session.add(g2)
    db_session.commit()
    _job(db_session, stranger_tenant, g2, stranger, "JOB-THEIRS", status="failed",
         erp_status="error")

    for email in (manager.email, "ta-n@example.com"):
        login(client, email)
        assert sorted(n["job_reference"] for n in _get(client)) == ["JOB-A", "JOB-B"], email


def test_a_platform_admin_is_told_only_about_their_own_jobs(client, db_session):
    """Admin sees every tenant - a bell of the whole platform's results would be noise."""
    tenant, op, other, group = _setup(db_session)
    admin = make_user(db_session, role="admin", email="admin-n@example.com")
    _job(db_session, tenant, group, op, "JOB-A", status="completed",
         ai_submitted_at=datetime.now(timezone.utc))
    login(client, admin.email)
    assert _get(client) == []


def test_marking_someone_elses_notification_read_is_ignored(client, db_session):
    tenant, op, other, group = _setup(db_session)
    _give_modes(db_session, op, other)
    theirs = _job(db_session, tenant, group, other, "JOB-THEIRS", status="failed",
                  erp_status="error")
    login(client, op.email)
    r = client.post("/api/v1/notifications/read", json={"ids": [f"failed:{theirs.id}"]})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 0
    assert db_session.query(NotificationRead).count() == 0


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


def test_failing_again_after_a_retry_is_a_new_unread_notification(client, db_session):
    """Read means read since it last happened. Retried and failed again, the job is a NEW
    failure - it must not arrive already greyed out, or no pop-up would ever announce it."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "JOB-AGAIN", status="failed", erp_status="error")
    login(client, op.email)
    assert client.post("/api/v1/notifications/read", json={}).json()["marked"] == 1
    # First failure ten minutes ago, read five minutes ago.
    now = datetime.now(timezone.utc)
    job.updated_at = now - timedelta(minutes=10)
    db_session.query(NotificationRead).one().read_at = now - timedelta(minutes=5)
    db_session.commit()
    assert _get(client)[0]["read"] is True

    # The retry fails again now: the job row moves forward (updated_at's onupdate), the read
    # mark does not.
    job.erp_reason = "Second attempt: the ERP rejected the HS code"
    db_session.commit()

    items = _get(client)
    assert items[0]["read"] is False
    assert items[0]["detail"] == "Second attempt: the ERP rejected the HS code"
    # Reading it again moves the one existing mark forward rather than piling up rows.
    assert client.post("/api/v1/notifications/read", json={}).json()["marked"] == 1
    assert _get(client)[0]["read"] is True
    assert db_session.query(NotificationRead).count() == 1


def test_a_super_admin_can_mark_their_own_notification_read(client, db_session):
    """A Super Admin has no tenant; the read mark takes the job's tenant (the column is NOT
    NULL), so this used to fail on insert."""
    tenant, op, other, group = _setup(db_session)
    sa = make_user(db_session, role="super_admin", email="sa-n@example.com")
    _job(db_session, tenant, group, sa, "JOB-SA", status="failed", erp_status="error")
    login(client, sa.email)
    r = client.post("/api/v1/notifications/read", json={})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1
    assert _get(client)[0]["read"] is True


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
