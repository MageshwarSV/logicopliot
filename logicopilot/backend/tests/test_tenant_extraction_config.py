"""GET/PUT /tenants/{id}/extraction-config and the tenant-scoped
/tenants/{id}/openai-key-pool CRUD - per-tenant extraction pause + OpenAI key routing,
independent of every other tenant and of the platform-wide switches on Settings.
Super Admin only."""
from unittest.mock import MagicMock, patch

from app.core.openai_pool import get_openai_client, get_pool_size, refresh_tenant_modes
from app.models.openai_api_key import OpenAIApiKey
from app.models.tenant_extraction_config import TenantExtractionConfig
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-extcfg@example.com")
    login(client, sa.email)
    return sa


def _ok_openai_mock():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    mock_cls.return_value.models.list.return_value = MagicMock()
    return patcher, mock_cls


# --- extraction-config --------------------------------------------------------------------

def test_get_returns_defaults_when_nothing_saved_yet(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.get(f"/api/v1/tenants/{tenant.id}/extraction-config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"extraction_paused": False, "openai_key_mode": "shared"}
    assert db_session.query(TenantExtractionConfig).count() == 0


def test_put_creates_the_row_on_first_save(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"extraction_paused": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"extraction_paused": True, "openai_key_mode": "shared"}
    assert db_session.query(TenantExtractionConfig).filter(
        TenantExtractionConfig.tenant_id == tenant.id).count() == 1


def test_put_only_changes_fields_actually_sent(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"extraction_paused": True})
    resp = client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"openai_key_mode": "own"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["openai_key_mode"] == "own"
    assert body["extraction_paused"] is True  # untouched by the second call


def test_put_rejects_an_invalid_key_mode(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"openai_key_mode": "dedicated"})
    assert resp.status_code == 400


def test_extraction_config_404s_for_an_unknown_tenant(client, db_session):
    _login_super_admin(client, db_session)
    resp = client.get("/api/v1/tenants/does-not-exist/extraction-config")
    assert resp.status_code == 404


def test_tenant_admin_cannot_read_or_write_extraction_config(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-extcfg@example.com")
    login(client, ta.email)

    assert client.get(f"/api/v1/tenants/{tenant.id}/extraction-config").status_code == 403
    assert client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"extraction_paused": True}).status_code == 403


def test_saving_key_mode_refreshes_the_in_process_pool_cache(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    resp = client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"openai_key_mode": "own"})
    assert resp.status_code == 200, resp.text
    # Reflected immediately in process memory, no restart - the same refresh-on-write
    # contract add_api_key/set_api_key_active/delete_api_key already follow.
    assert get_pool_size(tenant_id=tenant.id) == 1  # no keys yet - falls back to the single key


# --- tenant-scoped openai-key-pool ---------------------------------------------------------

def test_tenant_key_pool_is_empty_by_default(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    resp = client.get(f"/api/v1/tenants/{tenant.id}/openai-key-pool")
    assert resp.status_code == 200
    assert resp.json() == []


def test_add_tenant_key_stores_it_scoped_to_that_tenant_only(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    _login_super_admin(client, db_session)

    patcher, _ = _ok_openai_mock()
    try:
        resp = client.post(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool",
                            json={"label": "A's key", "api_key": "sk-proj-aaaaaaaaaaaaaaaaaaaa"})
    finally:
        patcher.stop()
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert row["label"] == "A's key"

    assert [k["label"] for k in client.get(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool").json()] == ["A's key"]
    # Never visible under a different tenant, and never in the global pool either.
    assert client.get(f"/api/v1/tenants/{tenant_b.id}/openai-key-pool").json() == []
    assert client.get("/api/v1/system-settings/openai-key-pool").json() == []
    stored = db_session.query(OpenAIApiKey).filter(OpenAIApiKey.tenant_id == tenant_a.id).one()
    assert stored.label == "A's key"


def test_pause_and_delete_are_scoped_to_the_right_tenant(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    _login_super_admin(client, db_session)

    patcher, _ = _ok_openai_mock()
    try:
        row = client.post(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool",
                           json={"label": "A's key", "api_key": "sk-proj-aaaaaaaaaaaaaaaaaaaa"}).json()
    finally:
        patcher.stop()

    # Another tenant's id cannot touch it - not found, not silently allowed.
    assert client.patch(f"/api/v1/tenants/{tenant_b.id}/openai-key-pool/{row['id']}",
                        json={"is_active": False}).status_code == 404
    assert client.delete(f"/api/v1/tenants/{tenant_b.id}/openai-key-pool/{row['id']}").status_code == 404

    resp = client.patch(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool/{row['id']}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    assert client.delete(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool/{row['id']}").status_code == 204
    assert client.get(f"/api/v1/tenants/{tenant_a.id}/openai-key-pool").json() == []


def test_tenant_admin_cannot_use_the_tenant_key_pool_endpoints(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-pool@example.com")
    login(client, ta.email)
    assert client.get(f"/api/v1/tenants/{tenant.id}/openai-key-pool").status_code == 403
    assert client.post(f"/api/v1/tenants/{tenant.id}/openai-key-pool",
                       json={"label": "x", "api_key": "sk-x"}).status_code == 403


# --- resolution: openai_pool.get_openai_client honors "own" mode --------------------------

def test_own_mode_with_a_key_routes_to_that_tenant_s_own_pool(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)

    patcher, _ = _ok_openai_mock()
    try:
        client.post(f"/api/v1/tenants/{tenant.id}/openai-key-pool",
                    json={"label": "A", "api_key": "sk-proj-aaaaaaaaaaaaaaaaaaaa"})
    finally:
        patcher.stop()
    client.put(f"/api/v1/tenants/{tenant.id}/extraction-config", json={"openai_key_mode": "own"})

    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10, tenant_id=tenant.id)
    tenant_key = db_session.query(OpenAIApiKey).filter(OpenAIApiKey.tenant_id == tenant.id).one()
    assert key_id == tenant_key.id
    # A call with no tenant_id (e.g. the Template Wizard) must never land on this tenant's key.
    with patch("openai.OpenAI"):
        _, default_key_id = get_openai_client(timeout=10)
    assert default_key_id == "default"


def test_own_mode_with_no_keys_falls_back_to_the_single_settings_key(db_session):
    tenant = make_tenant(db_session)
    db_session.add(TenantExtractionConfig(tenant_id=tenant.id, openai_key_mode="own"))
    db_session.commit()
    refresh_tenant_modes(db_session)

    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10, tenant_id=tenant.id)
    assert key_id == "default"


def test_shared_mode_tenant_never_uses_another_tenant_s_own_keys(client, db_session):
    tenant_owns = make_tenant(db_session, name="Tenant Owns")
    tenant_shared = make_tenant(db_session, name="Tenant Shared")
    _login_super_admin(client, db_session)

    patcher, _ = _ok_openai_mock()
    try:
        client.post(f"/api/v1/tenants/{tenant_owns.id}/openai-key-pool",
                    json={"label": "A", "api_key": "sk-proj-aaaaaaaaaaaaaaaaaaaa"})
    finally:
        patcher.stop()
    client.put(f"/api/v1/tenants/{tenant_owns.id}/extraction-config", json={"openai_key_mode": "own"})

    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10, tenant_id=tenant_shared.id)
    assert key_id == "default"  # stays on the global/default path, never tenant_owns's key
