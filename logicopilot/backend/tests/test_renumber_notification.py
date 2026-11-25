"""The bell tells the operator when the ERP gave their job a different number.

A rerun does not re-use the old ERP job number - the ERP files a brand new entry with a brand
new one. The job's reference follows it (see _adopt_erp_job_no), which is right, but it means
the number the operator was given yesterday is no longer this job's, and nothing told them.
Live, 2026-11-25: MAA/SI/03999/26-27 was re-run from the Entry Browser and filed as
MAA/SI/04008/26-27, while every screen still said 03999.

Derived from the job's own history rather than a column of its own, exactly like the other
kinds are derived from job state - so renames that already happened appear, with no backfill.
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.jobs import RENUMBER_NOTE
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from app.models.user import MANAGER, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(db_session):
    tenant = make_tenant(db_session)
    # Every login checks the tenant has an ACTIVE admin ("Your tenant's admin account is
    # disabled"), so a tenant without one cannot be logged into at all.
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-rn@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rn@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, email="op2-rn@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    return tenant, op, other, group


def _job(db_session, tenant, group, op, ref, **kw):
    kw.setdefault("erp_submitted_by_id", op.id)
    kw.setdefault("status", "completed")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref,
              created_by_id=op.id, assigned_operator_id=op.id, **kw)
    db_session.add(job)
    db_session.commit()
    return job


def _renamed(db_session, job, old, new, at=None):
    """The history line _adopt_erp_job_no writes when a rerun lands on a new number."""
    ev = JobEvent(tenant_id=job.tenant_id, job_id=job.id, status=job.status, stage=None,
                  note=f"{RENUMBER_NOTE}: {old} → {new}")
    db_session.add(ev)
    db_session.commit()
    if at is not None:
        ev.created_at = at
        db_session.commit()
    return ev


def _get(client):
    r = client.get("/api/v1/notifications")
    assert r.status_code == 200, r.text
    return r.json()


def _only(items, kind):
    return [n for n in items if n["kind"] == kind]


def test_a_renumbered_job_appears_on_the_bell(client, db_session):
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04008/26-27")
    login(client, op.email)

    got = _only(_get(client), "renumbered")
    assert len(got) == 1, f"expected one renumber notification, got {got}"
    assert got[0]["job_id"] == job.id


def test_it_names_both_numbers(client, db_session):
    """The new one alone is no use: the operator has an entry sitting in SOFTLINK under the
    OLD number that now needs deleting, and they need to be told which."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04008/26-27")
    login(client, op.email)

    n = _only(_get(client), "renumbered")[0]
    assert "MAA/SI/03999/26-27" in n["detail"]
    assert "MAA/SI/04008/26-27" in n["detail"]
    # And the reference is already the new one, so clicking through lands on the right job.
    assert n["job_reference"] == "MAA/SI/04008/26-27"


def test_a_job_re_run_twice_announces_only_its_latest_number(client, db_session):
    """Three entries exist in the ERP by now. The bell says what the number IS, not every
    number it has ever been - the full chain is in the job's history for anyone cleaning up."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    now = datetime.now(timezone.utc)
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04002/26-27",
             at=now - timedelta(hours=2))
    _renamed(db_session, job, "MAA/SI/04002/26-27", "MAA/SI/04008/26-27", at=now)
    login(client, op.email)

    got = _only(_get(client), "renumbered")
    assert len(got) == 1, f"one row per job, not per rename - got {got}"
    assert "04002" in got[0]["detail"] and "04008" in got[0]["detail"]


def test_a_colleagues_renumbered_job_is_not_on_your_bell(client, db_session):
    """Same rule as every other kind: you are told about the runs YOU submitted."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27",
               erp_submitted_by_id=other.id)
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04008/26-27")
    login(client, op.email)

    assert _only(_get(client), "renumbered") == []


def test_a_manager_sees_the_whole_tenants_renumbers(client, db_session):
    """Watching every ERP outcome IS the oversight roles' job - OVERSIGHT_ROLES."""
    tenant, op, other, group = _setup(db_session)
    mgr = make_user(db_session, role=MANAGER, tenant=tenant, email="mgr-rn@example.com")
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04008/26-27")
    login(client, mgr.email)

    assert len(_only(_get(client), "renumbered")) == 1


def test_dismissing_it_works_and_a_later_rerun_brings_it_back(client, db_session):
    """The same rule failures already follow: read means read SINCE it last happened. A job
    re-run again is a new number, so it must ask for attention again."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04002/26-27")
    now = datetime.now(timezone.utc)
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04002/26-27",
             at=now - timedelta(hours=2))
    login(client, op.email)

    r = client.post("/api/v1/notifications/read", json={"ids": [f"renumbered:{job.id}"]})
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1
    assert _only(_get(client), "renumbered")[0]["read"] is True

    job.reference = "MAA/SI/04008/26-27"
    db_session.commit()
    # Stamped AFTER the read mark, which is what a real second rerun is. Read is "read since
    # it last happened", so the comparison is against the mark written moments ago, not
    # against whenever this test started.
    _renamed(db_session, job, "MAA/SI/04002/26-27", "MAA/SI/04008/26-27",
             at=datetime.now(timezone.utc) + timedelta(seconds=1))

    again = _only(_get(client), "renumbered")[0]
    assert again["read"] is False, "a second rerun is a second thing to be told about"
    assert "04008" in again["detail"]


def test_an_ordinary_history_line_is_not_a_renumber(client, db_session):
    """The bell reads job history, which is full of other things. Only the rename marker
    counts - matched on its own prefix, not on anything a note might happen to contain."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status="completed",
                            stage="Completed", note="Moved on to Completed"))
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status="completed",
                            stage=None, note="Operator changed the ERP job number field"))
    db_session.commit()
    login(client, op.email)

    assert _only(_get(client), "renumbered") == []


def test_a_rename_older_than_the_window_drops_off(client, db_session):
    """WINDOW_DAYS. A notification about something from six weeks ago is a report, and the
    Jobs list already is one."""
    tenant, op, other, group = _setup(db_session)
    job = _job(db_session, tenant, group, op, "MAA/SI/04008/26-27")
    _renamed(db_session, job, "MAA/SI/03999/26-27", "MAA/SI/04008/26-27",
             at=datetime.now(timezone.utc) - timedelta(days=45))
    login(client, op.email)

    assert _only(_get(client), "renumbered") == []
