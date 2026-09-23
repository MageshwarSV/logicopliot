"""PATCH /jobs/{id}/eta — GK1 sets a target date for a job, drives the ETA boxes on the
operator dashboard."""
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_job(db_session, tenant, created_by_id=None):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ETA",
             status="extracted", created_by_id=created_by_id, assigned_operator_id=created_by_id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_operator_sets_own_jobs_eta(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-eta1@example.com")
    job = _make_job(db_session, tenant, created_by_id=op.id)
    login(client, op.email)

    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "2026-09-20"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["eta_date"] == "2026-09-20"
    db_session.refresh(job)
    assert job.eta_date == "2026-09-20"


def test_clearing_eta_sets_it_back_to_null(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-eta2@example.com")
    job = _make_job(db_session, tenant, created_by_id=op.id)
    login(client, op.email)

    client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "2026-09-20"})
    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["eta_date"] is None


def test_invalid_date_format_rejected(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-eta3@example.com")
    job = _make_job(db_session, tenant, created_by_id=op.id)
    login(client, op.email)

    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "20/09/2026"})
    assert resp.status_code == 400


def test_operator_cannot_set_eta_on_another_operators_job(client, db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="op-eta-owner@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, email="op-eta-other@example.com")
    job = _make_job(db_session, tenant, created_by_id=owner.id)
    login(client, other.email)

    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "2026-09-20"})
    assert resp.status_code == 404


def test_gk2_cannot_set_eta(client, db_session):
    tenant = make_tenant(db_session)
    job = _make_job(db_session, tenant)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-eta@example.com")
    gk2.assigned_modes = None
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "2026-09-20"})
    assert resp.status_code == 403


def test_eta_locked_read_only_operator_cannot_set_it(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    op = make_user(db_session, role="operator", tenant=tenant, email="op-eta-ro@example.com")
    job = _make_job(db_session, tenant, created_by_id=op.id)
    login(client, op.email)

    resp = client.patch(f"/api/v1/jobs/{job.id}/eta", json={"eta_date": "2026-09-20"})
    assert resp.status_code == 403
