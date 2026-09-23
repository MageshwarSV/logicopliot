from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup_two_tenants(db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    admin_a = make_user(db_session, role=TENANT_ADMIN, tenant=tenant_a, email="admin-a@example.com")
    admin_b = make_user(db_session, role=TENANT_ADMIN, tenant=tenant_b, email="admin-b@example.com")
    op_a = make_user(db_session, role=OPERATOR, tenant=tenant_a, email="op-a@example.com")
    op_b = make_user(db_session, role=OPERATOR, tenant=tenant_b, email="op-b@example.com")
    return tenant_a, tenant_b, admin_a, admin_b, op_a, op_b


def test_tenant_admin_never_sees_other_tenants_users(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    login(client, "admin-a@example.com")

    response = client.get("/api/v1/users")
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {"admin-a@example.com", "op-a@example.com"}
    assert "admin-b@example.com" not in emails
    assert "op-b@example.com" not in emails


def test_tenant_id_query_param_cannot_widen_scope(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    login(client, "admin-a@example.com")

    # Force-pass another tenant's id — must be ignored for non-super-admins.
    response = client.get(f"/api/v1/users?tenant_id={tenant_b.id}")
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {"admin-a@example.com", "op-a@example.com"}


def test_direct_id_lookup_of_other_tenants_user_returns_404_not_403(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    login(client, "admin-a@example.com")

    response = client.get(f"/api/v1/users/{op_b.id}")
    # 404, not 403 — must not confirm to a caller that a resource exists in another tenant.
    assert response.status_code == 404


def test_operator_cannot_see_other_tenants_users_either(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    login(client, "op-a@example.com")

    response = client.get("/api/v1/users")
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {"admin-a@example.com", "op-a@example.com"}


def test_super_admin_sees_across_all_tenants(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    response = client.get("/api/v1/users")
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {
        "sa@example.com",
        "admin-a@example.com",
        "admin-b@example.com",
        "op-a@example.com",
        "op-b@example.com",
    }


def test_super_admin_can_filter_to_one_tenant(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    response = client.get(f"/api/v1/users?tenant_id={tenant_a.id}")
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {"admin-a@example.com", "op-a@example.com"}


def test_super_admin_can_fetch_any_users_by_id(client, db_session):
    tenant_a, tenant_b, admin_a, admin_b, op_a, op_b = _setup_two_tenants(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    response = client.get(f"/api/v1/users/{op_b.id}")
    assert response.status_code == 200
    assert response.json()["email"] == "op-b@example.com"
