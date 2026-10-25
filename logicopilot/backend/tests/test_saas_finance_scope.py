"""get_tenant_scope grants saas_finance the same cross-tenant read behavior as super_admin/
admin (app/core/deps.py) - confirmed here against the two endpoints that go straight
through scoped_query with no other role check (list_tenants has its own explicit
require_role too; included for completeness)."""
from tests.conftest import login, make_tenant, make_user


def test_saas_finance_sees_every_tenant(client, db_session):
    t1 = make_tenant(db_session, name="Tenant One")
    t2 = make_tenant(db_session, name="Tenant Two")
    fin = make_user(db_session, role="saas_finance", email="fin-scope1@example.com")
    login(client, fin.email)

    resp = client.get("/api/v1/tenants")
    assert resp.status_code == 200, resp.text
    names = {t["name"] for t in resp.json()}
    assert {t1.name, t2.name}.issubset(names)


def test_saas_finance_can_look_up_any_tenant_by_id(client, db_session):
    tenant = make_tenant(db_session, name="Someone Else's Tenant")
    fin = make_user(db_session, role="saas_finance", email="fin-scope2@example.com")
    login(client, fin.email)

    resp = client.get(f"/api/v1/tenants/{tenant.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == tenant.name


def test_tenant_admin_still_cannot_look_up_another_tenant(client, db_session):
    """Regression guard: confirms the SAAS_FINANCE addition to get_tenant's bypass
    condition didn't accidentally loosen it for tenant_admin too."""
    own_tenant = make_tenant(db_session, name="Own Tenant")
    other_tenant = make_tenant(db_session, name="Other Tenant")
    ta = make_user(db_session, role="tenant_admin", tenant=own_tenant, email="ta-scope1@example.com")
    login(client, ta.email)

    resp = client.get(f"/api/v1/tenants/{other_tenant.id}")
    assert resp.status_code == 404
