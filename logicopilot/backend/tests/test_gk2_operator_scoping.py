"""A gk2 user's SECOND, independent scoping dimension - which GK1/operator users' jobs they
may see/act on (assigned_operator_ids), alongside the existing assigned_modes. Opposite null
semantics on purpose: null/empty here means unrestricted, so every gk2 account that existed
before this field was added keeps seeing everyone's jobs, unchanged. A fresh gk2 account is
given every CURRENT operator explicitly instead (so the checkbox list starts all-ticked and
genuinely narrows only once someone is unticked)."""
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, *, assigned_operator_id=None, gk2_status="pending"):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference="JOB-SCOPE", status="extracted",
        assigned_operator_id=assigned_operator_id, gk2_status=gk2_status,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_gk2(db_session, tenant, modes=None, assigned_operator_ids=None, email=None):
    user = make_user(db_session, role="gk2", tenant=tenant, email=email)
    user.assigned_modes = modes if modes is not None else ["Sea Import"]
    user.assigned_operator_ids = assigned_operator_ids
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login_tenant_admin(client, db_session, tenant):
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta@example.com")
    login(client, admin.email)
    return admin


# ---- create_user: defaulting / explicit list ------------------------------------------------

def test_new_gk2_user_defaults_to_every_current_operator(client, db_session):
    tenant = make_tenant(db_session)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    _login_tenant_admin(client, db_session, tenant)

    resp = client.post("/api/v1/users", json={
        "email": "newgk2@example.com", "password": "password123", "full_name": "New GK2",
        "role": "gk2", "modes": ["Sea Import"],
    })
    assert resp.status_code == 201, resp.text
    ids = set(resp.json()["assigned_operator_ids"])
    assert ids == {op1.id, op2.id}


def test_new_gk2_user_respects_an_explicit_operator_list(client, db_session):
    tenant = make_tenant(db_session)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    _login_tenant_admin(client, db_session, tenant)

    resp = client.post("/api/v1/users", json={
        "email": "newgk2b@example.com", "password": "password123", "full_name": "New GK2",
        "role": "gk2", "modes": ["Sea Import"], "assigned_operator_ids": [op1.id],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["assigned_operator_ids"] == [op1.id]


def test_new_gk2_user_respects_an_explicit_empty_operator_list(client, db_session):
    """An empty list is a deliberate choice ("no operators yet"), not treated as omitted."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    _login_tenant_admin(client, db_session, tenant)

    resp = client.post("/api/v1/users", json={
        "email": "newgk2c@example.com", "password": "password123", "full_name": "New GK2",
        "role": "gk2", "modes": ["Sea Import"], "assigned_operator_ids": [],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["assigned_operator_ids"] == []


# ---- update_user ------------------------------------------------------------------------------

def test_update_user_replaces_assigned_operator_ids(client, db_session):
    tenant = make_tenant(db_session)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    gk2 = _make_gk2(db_session, tenant, assigned_operator_ids=[op1.id, op2.id])
    _login_tenant_admin(client, db_session, tenant)

    resp = client.patch(f"/api/v1/users/{gk2.id}", json={"assigned_operator_ids": [op1.id]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_operator_ids"] == [op1.id]


# ---- list_jobs (GK2 queue) ---------------------------------------------------------------------

def test_gk2_scoped_to_one_operator_only_sees_that_operators_jobs(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    job1 = _make_job(db_session, tenant, group, assigned_operator_id=op1.id)
    job2 = _make_job(db_session, tenant, group, assigned_operator_id=op2.id)
    gk2 = _make_gk2(db_session, tenant, assigned_operator_ids=[op1.id])
    login(client, gk2.email)

    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert job1.id in ids
    assert job2.id not in ids


def test_gk2_with_no_operator_scoping_sees_every_operators_jobs(client, db_session):
    """The critical backward-compat case: a gk2 account whose assigned_operator_ids is still
    null (every account that existed before this field was added, or a fresh one explicitly
    left unrestricted) must keep seeing every operator's jobs, unchanged."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    job1 = _make_job(db_session, tenant, group, assigned_operator_id=op1.id)
    job2 = _make_job(db_session, tenant, group, assigned_operator_id=op2.id)
    gk2 = _make_gk2(db_session, tenant, assigned_operator_ids=None)
    login(client, gk2.email)

    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert job1.id in ids
    assert job2.id in ids


# ---- _load_job (per-job access gate, not just the list) ----------------------------------------

def test_gk2_cannot_open_a_job_outside_their_assigned_operators_by_known_id(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    job2 = _make_job(db_session, tenant, group, assigned_operator_id=op2.id)
    gk2 = _make_gk2(db_session, tenant, assigned_operator_ids=[op1.id])
    login(client, gk2.email)

    resp = client.get(f"/api/v1/jobs/{job2.id}")
    assert resp.status_code == 404


def test_gk2_can_still_open_a_job_from_an_assigned_operator(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    job1 = _make_job(db_session, tenant, group, assigned_operator_id=op1.id)
    gk2 = _make_gk2(db_session, tenant, assigned_operator_ids=[op1.id])
    login(client, gk2.email)

    resp = client.get(f"/api/v1/jobs/{job1.id}")
    assert resp.status_code == 200, resp.text
