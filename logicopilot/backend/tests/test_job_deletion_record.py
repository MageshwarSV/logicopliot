"""Who deleted a job is written down when it is deleted, and shown to a Super Admin only.

The Billing page counts "Deleted Jobs" from billing rows whose job no longer exists. Until now
there was no way to say who removed any of them - or whether a person did at all.
"""
from app.models.billable_page_event import BillablePageEvent
from app.models.job import Job
from app.models.job_deletion import JobDeletion
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _setup(db):
    tenant = make_tenant(db)
    make_user(db, role="super_admin", email="sa-del@example.com", full_name="Root Admin")
    admin = make_user(db, role="tenant_admin", tenant=tenant, email="ta-del@example.com",
                      full_name="Tina Admin")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready", mode="Sea Import")
    db.add(group)
    db.flush()
    jobs = []
    for ref in ("JOB-KEEP", "JOB-GONE"):
        job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, status="extracted",
                  created_by_id=admin.id)
        db.add(job)
        db.flush()
        db.add(BillablePageEvent(tenant_id=tenant.id, job_id=job.id, job_reference=ref,
                                 shipment_type="Sea Import", page_count=5))
        jobs.append(job)
    db.commit()
    return tenant, admin, jobs[0], jobs[1]


def _rows(client, tenant):
    r = client.get(f"/api/v1/tenants/{tenant.id}/billing/jobs")
    assert r.status_code == 200, r.text
    return {x["job_reference"]: x for x in r.json()["items"]}


def test_deleting_a_job_records_who_did_it(client, db_session):
    tenant, admin, keep, gone = _setup(db_session)
    gone_id = gone.id
    login(client, "ta-del@example.com")

    assert client.delete(f"/api/v1/jobs/{gone_id}").status_code == 204

    db_session.expire_all()
    assert db_session.get(Job, gone_id) is None
    rec = db_session.query(JobDeletion).one()
    assert (rec.job_id, rec.job_reference, rec.tenant_id) == (gone_id, "JOB-GONE", tenant.id)
    assert (rec.deleted_by_id, rec.deleted_by_name, rec.deleted_by_email, rec.deleted_by_role) == (
        admin.id, "Tina Admin", "ta-del@example.com", "tenant_admin")
    assert rec.how == "Deleted from the Jobs list" and rec.created_at is not None


def test_a_super_admin_sees_who_deleted_it_and_the_tenant_admin_does_not(client, db_session):
    tenant, admin, keep, gone = _setup(db_session)
    login(client, "ta-del@example.com")
    client.delete(f"/api/v1/jobs/{gone.id}")

    # the customer's own Tenant Admin reads the same table - and is told nothing
    theirs = _rows(client, tenant)
    assert theirs["JOB-GONE"]["status"] == "deleted"
    assert theirs["JOB-GONE"]["deleted_by"] is None and theirs["JOB-GONE"]["deleted_at"] is None

    client.post("/api/v1/auth/logout")
    login(client, "sa-del@example.com")
    ours = _rows(client, tenant)
    assert ours["JOB-GONE"]["deleted_by"] == "Tina Admin (ta-del@example.com)"
    assert ours["JOB-GONE"]["deleted_at"]
    # a live job has nobody to name
    assert ours["JOB-KEEP"]["status"] == "extracted"
    assert ours["JOB-KEEP"]["deleted_by"] is None and ours["JOB-KEEP"]["deleted_at"] is None


def test_a_job_deleted_before_this_was_recorded_names_nobody(client, db_session):
    tenant, admin, keep, gone = _setup(db_session)
    db_session.delete(gone)                      # gone the old way: no record written
    db_session.commit()
    login(client, "sa-del@example.com")

    row = _rows(client, tenant)["JOB-GONE"]
    assert row["status"] == "deleted" and row["deleted_by"] is None and row["deleted_at"] is None


def test_a_job_the_system_removed_says_so_instead_of_naming_a_person(client, db_session):
    from app.api.v1.jobs import _delete_job_cascade

    tenant, admin, keep, gone = _setup(db_session)
    _delete_job_cascade(db_session, gone, how="Removed automatically - a page filter left it with no documents")
    db_session.commit()
    login(client, "sa-del@example.com")

    row = _rows(client, tenant)["JOB-GONE"]
    assert row["deleted_by"] == "Removed automatically - a page filter left it with no documents"
    assert db_session.query(JobDeletion).one().deleted_by_id is None


def test_the_name_stays_as_it_was_after_the_account_is_removed(client, db_session):
    tenant, admin, keep, gone = _setup(db_session)
    login(client, "ta-del@example.com")
    client.delete(f"/api/v1/jobs/{gone.id}")
    client.post("/api/v1/auth/logout")

    rec = db_session.query(JobDeletion).one()
    rec.deleted_by_id = None                     # what ON DELETE SET NULL leaves behind
    db_session.commit()
    login(client, "sa-del@example.com")
    assert _rows(client, tenant)["JOB-GONE"]["deleted_by"] == "Tina Admin (ta-del@example.com)"
