"""Tenant.role_write_enabled — the "Masters" page's read-only lock for operator/gk2. A
missing/null setting means read-and-write (backward compatible); False blocks every write
endpoint for that role in that tenant, the same mechanism Manager always has."""
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode=None):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, *, status="extracted", created_by_id=None,
              validation_approved=False, gk2_status=None, gk2_validation_approved=False):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference="JOB-TEST",
        status=status, created_by_id=created_by_id,
        validation_approved=validation_approved, gk2_status=gk2_status,
        gk2_validation_approved=gk2_validation_approved,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# --------------------------------------------------------------- PATCH /tenants validation


def test_tenant_admin_sets_role_write_enabled_for_operator(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rw1@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"role_write_enabled": {"operator": False}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role_write_enabled"] == {"operator": False}


def test_role_write_enabled_rejects_manager_key(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rw2@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"role_write_enabled": {"manager": False}})
    assert resp.status_code == 400


def test_role_write_enabled_merges_not_replaces(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-rw3@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"role_write_enabled": {"operator": False}})
    assert resp.status_code == 200, resp.text
    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"role_write_enabled": {"gk2": False}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role_write_enabled"] == {"operator": False, "gk2": False}


# ------------------------------------------------------------------- enforcement, operator


def test_operator_locked_read_only_cannot_create_job(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw1@example.com")
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 403


def test_operator_locked_read_only_can_still_list_and_view_jobs(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw2@example.com")
    job = _make_job(db_session, tenant, group, created_by_id=op.id)
    login(client, op.email)

    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 200


def test_operator_locked_read_only_cannot_approve_validation(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw3@example.com")
    job = _make_job(db_session, tenant, group, created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/validation/approve")
    assert resp.status_code == 403


def test_operator_write_enabled_true_behaves_normally(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": True})
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw4@example.com")
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text


def test_operator_no_role_write_enabled_setting_behaves_normally(client, db_session):
    """Backward compatibility: a tenant that never touched the Masters page has no
    role_write_enabled at all — must default to full read-and-write, same as before this
    setting existed."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw5@example.com")
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text


def test_locking_operator_does_not_affect_gk2(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, validation_approved=True, gk2_status="pending",
                    gk2_validation_approved=True)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-rw1@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/validation/approve")
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------- enforcement, gk2


def test_gk2_locked_read_only_cannot_approve_document(client, db_session):
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session, role_write_enabled={"gk2": False})
    group = _make_group(db_session, tenant, mode="Sea Import")
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    job = _make_job(db_session, tenant, group, gk2_status="pending")
    jdoc = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path="/tmp/x.pdf", page_count=1)
    db_session.add(jdoc)
    db_session.commit()
    db_session.refresh(jdoc)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-rw2@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{jdoc.id}/approve", json={"approved": True})
    assert resp.status_code == 403


def test_gk2_locked_read_only_can_still_list_and_view_jobs(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"gk2": False})
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, validation_approved=True, gk2_status="pending")
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-rw3@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 200


def test_locking_gk2_does_not_affect_operator(client, db_session):
    tenant = make_tenant(db_session, role_write_enabled={"gk2": False})
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-rw6@example.com")
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text
