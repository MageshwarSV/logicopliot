"""OpenAI Admin API key management and /system-settings/openai-costs — the REAL dollar
figure and real token counts, as opposed to spend-analytics's character-based estimate.
Every OpenAI network call is mocked; these tests never touch the real network."""
from unittest.mock import patch

from app.core.openai_admin import OpenAIAdminAPIError
from tests.conftest import login, make_tenant, make_user


def test_non_super_admin_forbidden(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-oaic1@example.com")
    login(client, ta.email)

    assert client.get("/api/v1/system-settings/openai-costs").status_code == 403
    assert client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "x"}).status_code == 403


def test_not_connected_returns_zeros_not_an_error(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic1@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/openai-costs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is False
    assert body["total_usd"] == 0.0
    assert body["days"] == []


def test_set_admin_key_verifies_live_before_saving(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic2@example.com")
    login(client, sa.email)

    with patch("app.core.openai_admin.fetch_daily_costs", return_value={"2026-09-08": 1.23}):
        resp = client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-admin-123"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["openai_admin_key_set"] is True

    settings_resp = client.get("/api/v1/system-settings")
    assert settings_resp.json()["openai_admin_key_set"] is True


def test_set_admin_key_rejected_when_openai_says_no(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic3@example.com")
    login(client, sa.email)

    with patch("app.core.openai_admin.fetch_daily_costs",
              side_effect=OpenAIAdminAPIError("OpenAI Admin API returned 401: bad key")):
        resp = client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-bad"})
    assert resp.status_code == 400
    assert "401" in resp.json()["detail"]

    # Never saved — still not connected.
    settings_resp = client.get("/api/v1/system-settings")
    assert settings_resp.json()["openai_admin_key_set"] is False


def test_connected_key_returns_real_costs_and_tokens(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic4@example.com")
    login(client, sa.email)

    with patch("app.core.openai_admin.fetch_daily_costs", return_value={"2026-09-08": 3.5}):
        client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-admin-456"})

    with patch("app.api.v1.system_settings.fetch_daily_costs",
              return_value={"2026-09-08": 3.5, "2026-09-09": 2.5}), patch(
        "app.api.v1.system_settings.fetch_daily_token_usage",
        return_value={
            "2026-09-08": {"input_tokens": 26237000, "output_tokens": 390250},
            "2026-09-09": {"input_tokens": 1000, "output_tokens": 200},
        },
    ):
        resp = client.get("/api/v1/system-settings/openai-costs",
                          params={"start": "2026-09-08", "end": "2026-09-09"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True
    assert body["total_usd"] == 6.0
    assert body["total_input_tokens"] == 26238000
    assert body["total_output_tokens"] == 390450
    days = {d["date"]: d for d in body["days"]}
    assert days["2026-09-08"]["usd"] == 3.5
    assert days["2026-09-08"]["input_tokens"] == 26237000


def test_disconnect_admin_key(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic5@example.com")
    login(client, sa.email)
    with patch("app.core.openai_admin.fetch_daily_costs", return_value={}):
        client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-admin-789"})

    resp = client.delete("/api/v1/system-settings/openai-admin-key")
    assert resp.status_code == 200
    assert resp.json()["openai_admin_key_set"] is False
    assert client.get("/api/v1/system-settings").json()["openai_admin_key_set"] is False


def test_upstream_error_returns_502(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic6@example.com")
    login(client, sa.email)
    with patch("app.core.openai_admin.fetch_daily_costs", return_value={}):
        client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-admin-999"})

    with patch("app.api.v1.system_settings.fetch_daily_costs",
              side_effect=OpenAIAdminAPIError("Could not reach OpenAI: timed out")):
        resp = client.get("/api/v1/system-settings/openai-costs")
    assert resp.status_code == 502


def test_invalid_date_rejected(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oaic7@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/openai-costs", params={"start": "nope"})
    assert resp.status_code == 400
