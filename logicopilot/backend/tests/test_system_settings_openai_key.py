"""A Super Admin's own OpenAI API key, set from Settings instead of editing .env and
restarting. Verified live against OpenAI itself before being saved (a typo must never
silently replace a working key with a dead one), encrypted at rest, and never returned by
any endpoint once saved - not even masked, since a partial value is still something
"anyone can take": every read gets a yes/no only, changing it is write-only, and applied to
the running process immediately - get_settings() is lru_cache'd for the process lifetime,
so this only works if saving also clears that cache."""

import httpx
import pytest
from openai import AuthenticationError
from unittest.mock import MagicMock, patch

from app.core.config import get_settings
from app.core.system_settings import (
    InvalidOpenAIKey,
    apply_openai_api_key_override,
    get_system_settings,
    has_custom_openai_key,
    set_openai_api_key,
)
from tests.conftest import login, make_user


def _auth_error() -> AuthenticationError:
    resp = httpx.Response(401, request=httpx.Request("GET", "https://api.openai.com/v1/models"))
    return AuthenticationError("Incorrect API key provided", response=resp, body=None)


def test_has_custom_key_is_false_when_nothing_stored(db_session):
    assert has_custom_openai_key(db_session) is False


def test_setting_a_valid_key_stores_it_encrypted(db_session):
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.return_value = MagicMock()
        set_openai_api_key(db_session, "sk-proj-1234567890abcdefghijklmnop")

    row = get_system_settings(db_session)
    assert row.openai_api_key_encrypted is not None
    assert row.openai_api_key_encrypted != "sk-proj-1234567890abcdefghijklmnop"  # not plaintext
    assert has_custom_openai_key(db_session) is True


def test_setting_an_invalid_key_is_rejected_and_not_saved(db_session):
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.side_effect = _auth_error()
        with pytest.raises(InvalidOpenAIKey):
            set_openai_api_key(db_session, "sk-totally-wrong")

    assert get_system_settings(db_session).openai_api_key_encrypted is None
    assert has_custom_openai_key(db_session) is False


def test_setting_an_empty_key_is_rejected(db_session):
    with pytest.raises(InvalidOpenAIKey):
        set_openai_api_key(db_session, "   ")


def test_saving_a_new_key_applies_it_to_this_process_immediately(db_session, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.return_value = MagicMock()
        set_openai_api_key(db_session, "sk-proj-live-override-key-000000")

    assert get_settings().openai_api_key == "sk-proj-live-override-key-000000"


def test_apply_override_is_a_noop_when_nothing_stored(db_session, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    apply_openai_api_key_override(db_session)
    assert "OPENAI_API_KEY" not in __import__("os").environ


def test_apply_override_restores_a_previously_saved_key_on_startup(db_session, monkeypatch):
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.return_value = MagicMock()
        set_openai_api_key(db_session, "sk-proj-restored-on-startup-0000")

    # Simulate a fresh process: the env var is gone until startup re-applies it.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert get_settings().openai_api_key != "sk-proj-restored-on-startup-0000"

    apply_openai_api_key_override(db_session)
    assert get_settings().openai_api_key == "sk-proj-restored-on-startup-0000"


def test_openai_key_endpoint_requires_super_admin(client, db_session):
    from tests.conftest import make_tenant

    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-key@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/openai-key", json={"api_key": "sk-whatever"})
    assert resp.status_code == 403


def test_openai_key_endpoint_never_returns_any_part_of_the_key(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-key1@example.com")
    login(client, admin.email)
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.return_value = MagicMock()
        resp = client.post("/api/v1/system-settings/openai-key",
                           json={"api_key": "sk-proj-secret-value-should-never-leak"})
    assert resp.status_code == 200
    body_text = str(resp.json())
    assert "secret-value-should-never-leak" not in body_text
    assert "sk-proj" not in body_text  # not even a fragment/prefix
    assert resp.json() == {"openai_api_key_set": True}


def test_openai_key_endpoint_returns_400_for_a_rejected_key(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-key2@example.com")
    login(client, admin.email)
    with patch("openai.OpenAI") as mock_openai:
        mock_openai.return_value.models.list.side_effect = _auth_error()
        resp = client.post("/api/v1/system-settings/openai-key", json={"api_key": "sk-bad"})
    assert resp.status_code == 400


def test_system_settings_get_reports_key_status_as_boolean_only(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-key3@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/system-settings")
    assert resp.status_code == 200
    assert resp.json()["openai_api_key_set"] is False
