"""GET/PUT /tenants/{id}/billing-config - per-tenant pricing reference numbers for CARGORA's
own platform fee and AI page-processing tiers, and which TrackContainer.in plan a tenant is on.
Pure reference data, Super Admin only."""
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-billing@example.com")
    login(client, sa.email)
    return sa


def test_get_returns_the_standard_defaults_when_nothing_saved_yet(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cargora_monthly_fee"] == 4999.0
    assert body["cargora_tier1_rate_per_page"] == 2.50
    assert body["cargora_tier1_page_limit"] == 25000
    assert body["cargora_tier2_rate_per_page"] == 2.00
    assert body["trackcontainer_plan"] is None
    assert body["trackcontainer_starter_price"] == 2999.0
    assert body["trackcontainer_starter_container_limit"] == 500
    assert body["trackcontainer_professional_price"] == 5999.0
    assert body["trackcontainer_professional_container_limit"] == 1500
    assert body["gst_percent"] == 18.0
    # Nothing was actually created just by reading it.
    assert db_session.query(TenantBillingConfig).count() == 0


def test_put_creates_the_row_on_first_save(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={
        "cargora_monthly_fee": 3999.0, "trackcontainer_plan": "starter",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cargora_monthly_fee"] == 3999.0
    assert body["trackcontainer_plan"] == "starter"
    # Everything else keeps the model's own default, not left null.
    assert body["cargora_tier1_rate_per_page"] == 2.50
    assert body["gst_percent"] == 18.0
    assert db_session.query(TenantBillingConfig).filter(
        TenantBillingConfig.tenant_id == tenant.id).count() == 1


def test_put_only_changes_fields_actually_sent(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"cargora_monthly_fee": 3999.0})
    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"trackcontainer_plan": "professional"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["trackcontainer_plan"] == "professional"
    assert body["cargora_monthly_fee"] == 3999.0  # untouched by the second call


def test_put_rejects_an_invalid_trackcontainer_plan(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"trackcontainer_plan": "enterprise"})
    assert resp.status_code == 400


def test_put_can_clear_trackcontainer_plan_back_to_none(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"trackcontainer_plan": "starter"})
    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"trackcontainer_plan": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["trackcontainer_plan"] is None


def test_put_can_change_gst_percent(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"gst_percent": 12.0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gst_percent"] == 12.0


def test_billing_config_404s_for_an_unknown_tenant(client, db_session):
    _login_super_admin(client, db_session)
    resp = client.get("/api/v1/tenants/does-not-exist/billing-config")
    assert resp.status_code == 404


def test_tenant_admin_cannot_read_billing_config(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-billing@example.com")
    login(client, ta.email)

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing-config")
    assert resp.status_code == 403


def test_tenant_admin_cannot_write_billing_config(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-billing2@example.com")
    login(client, ta.email)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"cargora_monthly_fee": 1.0})
    assert resp.status_code == 403
