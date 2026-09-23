"""The Manager role: tenant-scoped, read-only observer over the whole tenant's jobs. Created
by the Tenant Admin like operator/gk2, but with no modes/templates and no write access
anywhere."""
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import User
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode=None):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, *, status="extracted", created_by_id=None,
              assigned_operator_id=None, validation_approved=False, gk2_status=None):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference="JOB-TEST",
        status=status, created_by_id=created_by_id, assigned_operator_id=assigned_operator_id,
        validation_approved=validation_approved, gk2_status=gk2_status,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# ---------------------------------------------------------------- creation


def test_tenant_admin_creates_a_manager(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-mgr1@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-manager@example.com", "password": "TestPass123",
        "full_name": "New Manager", "role": "manager",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "manager"
    assert resp.json()["modes"] is None


def test_manager_creation_rejects_modes(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-mgr2@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-manager-2@example.com", "password": "TestPass123",
        "full_name": "New Manager", "role": "manager", "modes": ["Sea Import"],
    })
    assert resp.status_code == 400


def test_manager_creation_rejects_template_ids(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-mgr3@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-manager-3@example.com", "password": "TestPass123",
        "full_name": "New Manager", "role": "manager", "template_ids": [group.id],
    })
    assert resp.status_code == 400


def test_operator_can_optionally_get_assigned_modes(client, db_session):
    """New this round: operator's modes are a tab-filter convenience, optional, no access
    effect - confirms assigned_modes round-trips for operator same as it does for gk2."""
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-opmodes@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-op-modes@example.com", "password": "TestPass123",
        "full_name": "New Operator", "role": "operator", "modes": ["Sea Import", "Air Export"],
    })
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json()["modes"]) == ["Air Export", "Sea Import"]

    # And without modes at all - still fine, unlike gk2 which requires at least one.
    resp2 = client.post("/api/v1/users", json={
        "email": "new-op-nomodes@example.com", "password": "TestPass123",
        "full_name": "New Operator 2", "role": "operator",
    })
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["modes"] is None


# ---------------------------------------------------------------- read-only visibility


def test_manager_sees_every_job_in_tenant_unrestricted(client, db_session):
    tenant = make_tenant(db_session)
    sea_group = _make_group(db_session, tenant, mode="Sea Import")
    air_group = _make_group(db_session, tenant, mode="Air Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-mgrsee@example.com")

    # A mix: assigned to a specific operator, still-in-review, already pending GK2, different
    # modes entirely - manager should see ALL of them, no filter at all.
    j1 = _make_job(db_session, tenant, sea_group, status="draft")
    j2 = _make_job(db_session, tenant, air_group, assigned_operator_id=op.id, status="extracted")
    j3 = _make_job(db_session, tenant, sea_group, status="extracted", validation_approved=True,
                   gk2_status="pending")

    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-see@example.com")
    login(client, manager.email)

    ids = {j["id"] for j in client.get("/api/v1/jobs").json()}
    assert ids == {j1.id, j2.id, j3.id}


def test_manager_confined_to_own_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    group_a = _make_group(db_session, tenant_a)
    group_b = _make_group(db_session, tenant_b)
    _make_job(db_session, tenant_a, group_a)
    job_b = _make_job(db_session, tenant_b, group_b)

    manager = make_user(db_session, role="manager", tenant=tenant_b, email="mgr-tenant@example.com")
    login(client, manager.email)

    ids = {j["id"] for j in client.get("/api/v1/jobs").json()}
    assert ids == {job_b.id}


def test_manager_can_load_jobs_page_without_403(client, db_session):
    """Regression-shaped like GK2's own: the jobs page calls list + available-groups
    together (Promise.all) - either 403'ing breaks the whole page."""
    tenant = make_tenant(db_session)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-page@example.com")
    login(client, manager.email)

    assert client.get("/api/v1/jobs").status_code == 200
    assert client.get("/api/v1/available-groups").status_code == 200


def test_manager_can_open_a_job_detail(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-detail@example.com")
    login(client, manager.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- strictly read-only


def test_manager_cannot_approve_document(client, db_session):
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    job = _make_job(db_session, tenant, group)
    jdoc = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path="/tmp/x.pdf", page_count=1)
    db_session.add(jdoc)
    db_session.commit()
    db_session.refresh(jdoc)

    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-noapprove@example.com")
    login(client, manager.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{jdoc.id}/approve", json={"approved": True})
    assert resp.status_code == 403


def test_manager_cannot_record_stage(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-nostage@example.com")
    login(client, manager.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/stage", json={"stage": "Dump Data"})
    assert resp.status_code == 403


def test_manager_cannot_create_job(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-nocreate@example.com")
    login(client, manager.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 403


def test_manager_cannot_complete_job(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-nocomplete@example.com")
    login(client, manager.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/complete")
    assert resp.status_code == 403


def test_manager_cannot_correct_field_value(client, db_session):
    tenant = make_tenant(db_session)
    manager = make_user(db_session, role="manager", tenant=tenant, email="mgr-nocorrect@example.com")
    login(client, manager.email)

    resp = client.patch("/api/v1/job-field-values/nonexistent-id", json={"corrected_value": "x"})
    assert resp.status_code == 403  # role check fires before the 404 the id would otherwise get


# ---------------------------------------------------------------- deletion cascade


def test_deleting_tenant_admin_cascades_to_their_manager_accounts(client, db_session):
    tenant = make_tenant(db_session)
    super_admin = make_user(db_session, role="super_admin", email="sa-cascade@example.com")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-cascade@example.com")
    login(client, ta.email)
    resp = client.post("/api/v1/users", json={
        "email": "cascade-manager@example.com", "password": "TestPass123",
        "full_name": "Cascade Manager", "role": "manager",
    })
    assert resp.status_code == 201
    manager_id = resp.json()["id"]

    login(client, super_admin.email)
    resp = client.delete(f"/api/v1/users/{ta.id}")
    assert resp.status_code == 204

    assert db_session.get(User, manager_id) is None
