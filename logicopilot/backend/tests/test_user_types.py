"""Tenant Admin's own custom user types — a name layered on top of operator/gk2/manager,
with its own Read/Write rule instead of the tenant-wide default for that base role."""
from app.models.template_group import TemplateGroup
from app.models.user_type import UserType
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_type(db_session, tenant, *, name="Supervisor", base_role="operator", write_enabled=True):
    ut = UserType(tenant_id=tenant.id, name=name, base_role=base_role, write_enabled=write_enabled)
    db_session.add(ut)
    db_session.commit()
    db_session.refresh(ut)
    return ut


# ------------------------------------------------------------------------------- CRUD


def test_tenant_admin_creates_custom_type(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut1@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/user-types", json={"name": "Supervisor", "base_role": "operator"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Supervisor"
    assert body["base_role"] == "operator"
    assert body["write_enabled"] is True


def test_duplicate_name_in_same_tenant_rejected(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut2@example.com")
    login(client, ta.email)

    client.post("/api/v1/user-types", json={"name": "Supervisor", "base_role": "operator"})
    resp = client.post("/api/v1/user-types", json={"name": "supervisor", "base_role": "gk2"})
    assert resp.status_code == 409


def test_manager_based_type_is_always_write_locked(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut3@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/user-types", json={"name": "Auditor", "base_role": "manager", "write_enabled": True})
    assert resp.status_code == 201, resp.text
    assert resp.json()["write_enabled"] is False


def test_invalid_base_role_rejected(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut4@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/user-types", json={"name": "X", "base_role": "tenant_admin"})
    assert resp.status_code == 422


def test_update_write_enabled(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut5@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant)

    resp = client.patch(f"/api/v1/user-types/{ut.id}", json={"write_enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["write_enabled"] is False


def test_update_manager_based_type_stays_locked(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut6@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant, base_role="manager", write_enabled=False)

    resp = client.patch(f"/api/v1/user-types/{ut.id}", json={"write_enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["write_enabled"] is False


def test_delete_blocked_while_in_use(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut7@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ut1@example.com")
    op.user_type_id = ut.id
    db_session.add(op)
    db_session.commit()

    resp = client.delete(f"/api/v1/user-types/{ut.id}")
    assert resp.status_code == 400


def test_delete_succeeds_when_unused(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut8@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant)

    resp = client.delete(f"/api/v1/user-types/{ut.id}")
    assert resp.status_code == 204


def test_list_scoped_to_own_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="A")
    tenant_b = make_tenant(db_session, name="B")
    _make_type(db_session, tenant_a, name="OnlyA")
    _make_type(db_session, tenant_b, name="OnlyB")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant_a, email="ta-ut9@example.com")
    login(client, ta.email)

    resp = client.get("/api/v1/user-types")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert names == ["OnlyA"]


# ------------------------------------------------------------------- creating users under a type


def test_create_user_under_custom_type_sets_role_and_type(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut10@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant, name="Supervisor", base_role="operator")

    resp = client.post("/api/v1/users", json={
        "email": "sup1@example.com", "password": "password123", "full_name": "Sup One",
        "role": "operator", "user_type_id": ut.id,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "operator"
    assert body["user_type_id"] == ut.id
    assert body["user_type_name"] == "Supervisor"


def test_create_user_under_custom_type_ignores_mismatched_role(client, db_session):
    """The type's own base_role wins — a caller can't send role="manager" to sneak past the
    GK2-modes-required check while actually being created under a gk2-based custom type."""
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-ut11@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant, name="Reviewer", base_role="gk2")

    resp = client.post("/api/v1/users", json={
        "email": "rev1@example.com", "password": "password123", "full_name": "Rev One",
        "role": "manager", "user_type_id": ut.id, "modes": ["Sea Import"],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "gk2"


def test_create_user_under_custom_type_from_other_tenant_rejected(client, db_session):
    tenant_a = make_tenant(db_session, name="A2")
    tenant_b = make_tenant(db_session, name="B2")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant_a, email="ta-ut12@example.com")
    login(client, ta.email)
    ut = _make_type(db_session, tenant_b, name="Foreign")

    resp = client.post("/api/v1/users", json={
        "email": "x1@example.com", "password": "password123", "full_name": "X",
        "role": "operator", "user_type_id": ut.id,
    })
    assert resp.status_code == 400


# ------------------------------------------------------------------------ write enforcement


def test_custom_type_write_disabled_blocks_write(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    ut = _make_type(db_session, tenant, base_role="operator", write_enabled=False)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ut2@example.com")
    op.user_type_id = ut.id
    db_session.add(op)
    db_session.commit()
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 403


def test_custom_type_write_enabled_allows_write(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    ut = _make_type(db_session, tenant, base_role="operator", write_enabled=True)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ut3@example.com")
    op.user_type_id = ut.id
    db_session.add(op)
    db_session.commit()
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text


def test_custom_type_overrides_tenant_wide_default(client, db_session):
    """The tenant's blanket role_write_enabled["operator"]=False would normally lock every
    operator — a custom type's own write_enabled=True carves its members back out."""
    tenant = make_tenant(db_session, role_write_enabled={"operator": False})
    group = _make_group(db_session, tenant)
    ut = _make_type(db_session, tenant, base_role="operator", write_enabled=True)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ut4@example.com")
    op.user_type_id = ut.id
    db_session.add(op)
    db_session.commit()
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text


def test_plain_operator_unaffected_by_others_custom_type(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_type(db_session, tenant, base_role="operator", write_enabled=False)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ut5@example.com")
    login(client, op.email)

    resp = client.post("/api/v1/jobs", json={"group_id": group.id})
    assert resp.status_code == 201, resp.text
