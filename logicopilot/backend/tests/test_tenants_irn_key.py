"""Super Admin dashboard's "Generate Link" button (POST /tenants/{id}/irn-pending-key) - the
one place a tenant's own IRN Pending key (Tenant.irn_pending_access_key) gets created."""
from app.models.tenant import Tenant
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session):
    admin = make_user(db_session, role="super_admin", email="admin@example.com")
    login(client, admin.email)
    return admin


def test_generate_creates_a_key_for_a_tenant_that_has_none(client, db_session):
    tenant = make_tenant(db_session)
    assert tenant.irn_pending_access_key is None
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/tenants/{tenant.id}/irn-pending-key")
    assert resp.status_code == 200, resp.text
    key = resp.json()["irn_pending_access_key"]
    assert key

    db_session.refresh(tenant)
    assert tenant.irn_pending_access_key == key


def test_generate_again_returns_the_same_key_instead_of_rotating_it(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    first = client.post(f"/api/v1/tenants/{tenant.id}/irn-pending-key").json()["irn_pending_access_key"]
    second = client.post(f"/api/v1/tenants/{tenant.id}/irn-pending-key").json()["irn_pending_access_key"]
    assert first == second


def test_two_tenants_get_different_keys(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    _login_super_admin(client, db_session)

    key_a = client.post(f"/api/v1/tenants/{tenant_a.id}/irn-pending-key").json()["irn_pending_access_key"]
    key_b = client.post(f"/api/v1/tenants/{tenant_b.id}/irn-pending-key").json()["irn_pending_access_key"]
    assert key_a != key_b


def test_generate_404s_for_an_unknown_tenant(client, db_session):
    _login_super_admin(client, db_session)

    resp = client.post("/api/v1/tenants/does-not-exist/irn-pending-key")
    assert resp.status_code == 404


def test_generate_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/tenants/{tenant.id}/irn-pending-key")
    assert resp.status_code == 403


def test_list_tenants_includes_the_key(client, db_session):
    tenant = make_tenant(db_session)
    tenant.irn_pending_access_key = "already-set"
    db_session.commit()
    _login_super_admin(client, db_session)

    resp = client.get("/api/v1/tenants")
    assert resp.status_code == 200, resp.text
    row = next(t for t in resp.json() if t["id"] == tenant.id)
    assert row["irn_pending_access_key"] == "already-set"
