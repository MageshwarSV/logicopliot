"""PATCH /tenants/{id} — lets a Tenant Admin manage their own tenant's allowed_modes
(the "Shipment Type" page), without needing Super Admin access; still tenant-isolated."""
from tests.conftest import login, make_tenant, make_user


def test_tenant_admin_updates_own_allowed_modes(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-modes1@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"allowed_modes": ["Sea Import", "Air Export"]})
    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["allowed_modes"]) == ["Air Export", "Sea Import"]


def test_tenant_admin_cannot_update_another_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant_a, email="ta-modes2@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant_b.id}", json={"allowed_modes": ["Sea Import"]})
    assert resp.status_code == 404


def test_tenant_admin_update_rejects_invalid_mode(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-modes3@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"allowed_modes": ["Not A Real Mode"]})
    assert resp.status_code == 400


def test_super_admin_can_update_any_tenant(client, db_session):
    tenant = make_tenant(db_session)
    sa = make_user(db_session, role="super_admin", email="sa-modes@example.com")
    login(client, sa.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"allowed_modes": ["Sea Export"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed_modes"] == ["Sea Export"]


def test_operator_cannot_update_tenant(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-modes@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"allowed_modes": ["Sea Import"]})
    assert resp.status_code == 403


def test_empty_allowed_modes_clears_restriction(client, db_session):
    tenant = make_tenant(db_session, allowed_modes=["Sea Import"])
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-modes4@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"allowed_modes": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["allowed_modes"] is None
