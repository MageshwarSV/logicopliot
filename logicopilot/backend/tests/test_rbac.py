from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def test_super_admin_only_endpoint_rejects_tenant_admin(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.post("/api/v1/tenants", json={"name": "New Tenant"})
    assert response.status_code == 403


def test_super_admin_only_endpoint_rejects_operator(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    login(client, "op@example.com")

    response = client.post("/api/v1/tenants", json={"name": "New Tenant"})
    assert response.status_code == 403


def test_super_admin_can_create_tenant(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    response = client.post("/api/v1/tenants", json={"name": "New Tenant"})
    assert response.status_code == 201


def test_tenant_admin_can_create_operator(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "new-op@example.com",
            "password": "OpPass1234",
            "full_name": "New Operator",
            "role": OPERATOR,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == OPERATOR
    assert body["tenant_id"] == tenant.id


def test_tenant_admin_cannot_create_tenant_admin(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "sneaky@example.com",
            "password": "OpPass1234",
            "full_name": "Sneaky",
            "role": TENANT_ADMIN,
        },
    )
    assert response.status_code == 403


def test_tenant_admin_cannot_escape_own_tenant_via_payload(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant_a, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "new-op@example.com",
            "password": "OpPass1234",
            "full_name": "New Operator",
            "role": OPERATOR,
            "tenant_id": tenant_b.id,
        },
    )
    assert response.status_code == 201
    # tenant_id in the payload must be ignored — the operator is created under the caller's
    # own tenant regardless of what was passed.
    assert response.json()["tenant_id"] == tenant_a.id


def test_operator_cannot_create_any_user(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    login(client, "op@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "another@example.com",
            "password": "OpPass1234",
            "full_name": "Another",
            "role": OPERATOR,
        },
    )
    assert response.status_code == 403


def test_super_admin_creating_user_requires_tenant_admin_role(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tenant = make_tenant(db_session, name="Tenant A")
    login(client, "sa@example.com")

    response = client.post(
        "/api/v1/users",
        json={
            "email": "op@example.com",
            "password": "OpPass1234",
            "full_name": "Operator",
            "role": OPERATOR,
            "tenant_id": tenant.id,
        },
    )
    assert response.status_code == 403


def test_tenant_admin_can_deactivate_own_operator(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    login(client, "ta@example.com")

    response = client.patch(f"/api/v1/users/{op.id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_tenant_admin_cannot_deactivate_operator_in_other_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant_a, email="ta@example.com")
    op_b = make_user(db_session, role=OPERATOR, tenant=tenant_b, email="op-b@example.com")
    login(client, "ta@example.com")

    response = client.patch(f"/api/v1/users/{op_b.id}", json={"is_active": False})
    assert response.status_code == 404


def test_tenant_admin_cannot_deactivate_another_tenant_admin(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    other_ta = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta2@example.com")
    login(client, "ta@example.com")

    response = client.patch(f"/api/v1/users/{other_ta.id}", json={"is_active": False})
    assert response.status_code == 403


def test_super_admin_can_deactivate_tenant_admin(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "sa@example.com")

    response = client.patch(f"/api/v1/users/{ta.id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_super_admin_cannot_deactivate_operator_directly(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op@example.com")
    login(client, "sa@example.com")

    response = client.patch(f"/api/v1/users/{op.id}", json={"is_active": False})
    assert response.status_code == 403


def test_user_cannot_deactivate_self(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")
    me = client.get("/api/v1/auth/me").json()

    response = client.patch(f"/api/v1/users/{me['id']}", json={"is_active": False})
    assert response.status_code == 400


def test_get_own_tenant_by_id(client, db_session):
    tenant = make_tenant(db_session, name="Tenant A")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.get(f"/api/v1/tenants/{tenant.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Tenant A"


def test_cannot_get_other_tenant_by_id(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant_a, email="ta@example.com")
    login(client, "ta@example.com")

    response = client.get(f"/api/v1/tenants/{tenant_b.id}")
    assert response.status_code == 404
