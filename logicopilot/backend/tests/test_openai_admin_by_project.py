"""Per-project OpenAI spend - this server runs several apps under what may be one shared
OpenAI organization, so the plain /openai-costs total can silently include usage that has
nothing to do with Logicopilot. fetch_daily_costs_by_project/fetch_projects and the
/openai-costs-by-project endpoint tell the pieces apart by the project each spend line
actually belongs to. Every OpenAI network call is mocked; these tests never touch the
real network."""
import json
from unittest.mock import MagicMock, patch

from app.core.openai_admin import fetch_daily_costs_by_project, fetch_projects
from tests.conftest import login, make_user


def _fake_response(body: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_fetch_daily_costs_by_project_groups_by_project_id():
    body = {
        "data": [
            {"start_time": 1757203200, "results": [
                {"project_id": "proj_logicopilot", "amount": {"value": 1.5}},
                {"project_id": "proj_other_app", "amount": {"value": 4.0}},
            ]},
        ],
        "has_more": False,
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        from datetime import date
        result = fetch_daily_costs_by_project("sk-admin", date(2025, 9, 7), date(2025, 9, 7))
    assert result == {
        "proj_logicopilot": {"2025-09-07": 1.5},
        "proj_other_app": {"2025-09-07": 4.0},
    }


def test_fetch_daily_costs_by_project_sends_group_by_param():
    body = {"data": [], "has_more": False}
    captured_urls = []

    def _urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return _fake_response(body)

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        from datetime import date
        fetch_daily_costs_by_project("sk-admin", date(2026, 9, 7), date(2026, 9, 7))
    assert captured_urls
    assert "group_by=project_id" in captured_urls[0]


def test_fetch_projects_maps_id_to_name():
    body = {"data": [
        {"id": "proj_logicopilot", "name": "Logicopilot"},
        {"id": "proj_other_app", "name": "Other App"},
    ], "has_more": False}
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        result = fetch_projects("sk-admin")
    assert result == {"proj_logicopilot": "Logicopilot", "proj_other_app": "Other App"}


def test_fetch_projects_falls_back_to_id_when_name_missing():
    body = {"data": [{"id": "proj_x"}], "has_more": False}
    with patch("urllib.request.urlopen", return_value=_fake_response(body)):
        result = fetch_projects("sk-admin")
    assert result == {"proj_x": "proj_x"}


def test_endpoint_resolves_project_names_and_sorts_by_spend(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oabp1@example.com")
    login(client, sa.email)
    with patch("app.core.openai_admin.fetch_daily_costs", return_value={}):
        client.post("/api/v1/system-settings/openai-admin-key", json={"api_key": "sk-admin-abp"})

    with patch("app.api.v1.system_settings.fetch_daily_costs_by_project", return_value={
        "proj_logicopilot": {"2026-09-08": 1.5},
        "proj_other_app": {"2026-09-08": 25.0},
    }), patch("app.api.v1.system_settings.fetch_projects", return_value={
        "proj_logicopilot": "Logicopilot", "proj_other_app": "Other App",
    }):
        resp = client.get("/api/v1/system-settings/openai-costs-by-project",
                          params={"start": "2026-09-08", "end": "2026-09-08"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True
    assert body["total_usd"] == 26.5
    # Sorted highest spend first.
    assert body["projects"][0] == {
        "project_id": "proj_other_app", "project_name": "Other App", "usd": 25.0,
    }
    assert body["projects"][1] == {
        "project_id": "proj_logicopilot", "project_name": "Logicopilot", "usd": 1.5,
    }


def test_endpoint_not_connected_returns_zeros(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-oabp2@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/openai-costs-by-project")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is False
    assert body["projects"] == []


def test_endpoint_forbidden_for_non_super_admin(client, db_session):
    from tests.conftest import make_tenant
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-oabp3@example.com")
    login(client, ta.email)
    assert client.get("/api/v1/system-settings/openai-costs-by-project").status_code == 403
