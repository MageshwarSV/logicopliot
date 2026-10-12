"""A pool of OpenAI API keys a Super Admin can add (app/core/openai_pool.py), round-robinned
across so one OpenAI Project's TPM/RPM ceiling does not block every call. With no keys added,
every behavior must stay identical to the single Settings key (today's only mode) - that is
the central guarantee this file exists to protect."""

from unittest.mock import MagicMock, patch

import pytest
from openai import AuthenticationError

from app.core.openai_pool import (
    add_api_key,
    delete_api_key,
    get_openai_client,
    get_pool_size,
    list_api_keys,
    mark_key_cooldown,
    refresh_pool,
    set_api_key_active,
)
from app.core.system_settings import InvalidOpenAIKey
from tests.conftest import login, make_tenant, make_user


def _auth_error() -> AuthenticationError:
    import httpx

    resp = httpx.Response(401, request=httpx.Request("GET", "https://api.openai.com/v1/models"))
    return AuthenticationError("Incorrect API key provided", response=resp, body=None)


def _ok_openai_mock():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    mock_cls.return_value.models.list.return_value = MagicMock()
    return patcher, mock_cls


# --- CRUD -------------------------------------------------------------------------------

def test_add_api_key_stores_it_encrypted_and_puts_it_in_the_pool(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        row = add_api_key(db_session, "Project A", "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    assert row.label == "Project A"
    assert row.key_encrypted != "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa"  # not plaintext
    assert row.is_active is True
    assert get_pool_size() == 1


def test_add_api_key_rejects_a_key_openai_itself_rejects(db_session):
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    try:
        mock_cls.return_value.models.list.side_effect = _auth_error()
        with pytest.raises(InvalidOpenAIKey):
            add_api_key(db_session, "Bad key", "sk-bad")
    finally:
        patcher.stop()
    assert list_api_keys(db_session) == []


def test_add_api_key_requires_a_label(db_session):
    with pytest.raises(InvalidOpenAIKey):
        add_api_key(db_session, "   ", "sk-proj-whatever-aaaaaaaaaaaaaa")


def test_list_api_keys_is_ordered_by_sort_order(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        add_api_key(db_session, "First", "sk-proj-first-aaaaaaaaaaaaaaaa")
        add_api_key(db_session, "Second", "sk-proj-second-aaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    labels = [k.label for k in list_api_keys(db_session)]
    assert labels == ["First", "Second"]


def test_set_api_key_active_false_takes_it_out_of_the_pool(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        row = add_api_key(db_session, "Project A", "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    assert get_pool_size() == 1

    set_api_key_active(db_session, row.id, False)
    assert get_pool_size() == 1  # falls back to the single default key, never 0
    with patch("openai.OpenAI"):
        client, key_id = get_openai_client(timeout=10)
    assert key_id == "default"


def test_set_api_key_active_on_an_unknown_id_returns_none(db_session):
    assert set_api_key_active(db_session, "nope", True) is None


def test_delete_api_key_removes_it_from_the_pool(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        row = add_api_key(db_session, "Project A", "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    assert delete_api_key(db_session, row.id) is True
    assert list_api_keys(db_session) == []
    assert get_pool_size() == 1  # back to the single-key fallback


def test_delete_api_key_on_an_unknown_id_returns_false(db_session):
    assert delete_api_key(db_session, "nope") is False


# --- get_openai_client / round robin / cooldown ------------------------------------------

def test_get_openai_client_falls_back_to_the_single_key_with_an_empty_pool(db_session, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-default")
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        with patch("openai.OpenAI") as mock_cls:
            client, key_id = get_openai_client(timeout=15)
        assert key_id == "default"
        mock_cls.assert_called_once_with(api_key="sk-env-default", timeout=15)
    finally:
        get_settings.cache_clear()


def test_get_openai_client_round_robins_through_an_active_pool(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        a = add_api_key(db_session, "A", "sk-proj-a-aaaaaaaaaaaaaaaaaaaaa")
        b = add_api_key(db_session, "B", "sk-proj-b-aaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()

    with patch("openai.OpenAI"):
        seen = [get_openai_client(timeout=10)[1] for _ in range(4)]
    assert seen == [a.id, b.id, a.id, b.id]


def test_get_openai_client_skips_a_key_on_cooldown(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        a = add_api_key(db_session, "A", "sk-proj-a-aaaaaaaaaaaaaaaaaaaaa")
        b = add_api_key(db_session, "B", "sk-proj-b-aaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()

    mark_key_cooldown(a.id, seconds=120)
    with patch("openai.OpenAI"):
        seen = [get_openai_client(timeout=10)[1] for _ in range(3)]
    assert seen == [b.id, b.id, b.id]


def test_get_openai_client_uses_the_soonest_cooldown_when_every_key_is_cooling(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        a = add_api_key(db_session, "A", "sk-proj-a-aaaaaaaaaaaaaaaaaaaaa")
        b = add_api_key(db_session, "B", "sk-proj-b-aaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()

    mark_key_cooldown(a.id, seconds=500)
    mark_key_cooldown(b.id, seconds=10)
    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10)
    assert key_id == b.id  # clears soonest


def test_mark_key_cooldown_is_a_noop_for_the_default_key():
    # The single Settings key has no sibling to fail over to - nothing to skip.
    mark_key_cooldown("default", seconds=999)
    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10)
    assert key_id == "default"


def test_refresh_pool_clears_cooldowns_and_rotation_state(db_session):
    patcher, _ = _ok_openai_mock()
    try:
        a = add_api_key(db_session, "A", "sk-proj-a-aaaaaaaaaaaaaaaaaaaaa")
    finally:
        patcher.stop()
    mark_key_cooldown(a.id, seconds=999)
    refresh_pool(db_session)
    with patch("openai.OpenAI"):
        _, key_id = get_openai_client(timeout=10)
    assert key_id == a.id  # cooldown was cleared by the refresh


# --- API endpoints ------------------------------------------------------------------------

def test_endpoints_require_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-pool@example.com")
    login(client, admin.email)
    assert client.get("/api/v1/system-settings/openai-key-pool").status_code == 403
    assert client.post("/api/v1/system-settings/openai-key-pool",
                       json={"label": "x", "api_key": "sk-x"}).status_code == 403


def test_add_list_and_never_leak_the_key_value(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pool1@example.com")
    login(client, admin.email)
    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value.models.list.return_value = MagicMock()
        resp = client.post("/api/v1/system-settings/openai-key-pool",
                           json={"label": "Project A", "api_key": "sk-proj-super-secret-value"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["label"] == "Project A"
    assert "super-secret-value" not in str(body)

    listed = client.get("/api/v1/system-settings/openai-key-pool").json()
    assert len(listed) == 1
    assert "super-secret-value" not in str(listed)


def test_add_endpoint_returns_400_for_a_rejected_key(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pool2@example.com")
    login(client, admin.email)
    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value.models.list.side_effect = _auth_error()
        resp = client.post("/api/v1/system-settings/openai-key-pool",
                           json={"label": "Bad", "api_key": "sk-bad"})
    assert resp.status_code == 400


def test_patch_toggles_active_and_delete_removes(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pool3@example.com")
    login(client, admin.email)
    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value.models.list.return_value = MagicMock()
        created = client.post("/api/v1/system-settings/openai-key-pool",
                              json={"label": "Project A", "api_key": "sk-proj-aaaaaaaaaaaaaaaaa"}).json()
    key_id = created["id"]

    resp = client.patch(f"/api/v1/system-settings/openai-key-pool/{key_id}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp = client.delete(f"/api/v1/system-settings/openai-key-pool/{key_id}")
    assert resp.status_code == 204
    assert client.get("/api/v1/system-settings/openai-key-pool").json() == []


def test_patch_and_delete_404_for_an_unknown_key(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pool4@example.com")
    login(client, admin.email)
    assert client.patch("/api/v1/system-settings/openai-key-pool/nope",
                        json={"is_active": False}).status_code == 404
    assert client.delete("/api/v1/system-settings/openai-key-pool/nope").status_code == 404


def test_system_settings_get_reports_active_pool_count(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pool5@example.com")
    login(client, admin.email)
    assert client.get("/api/v1/system-settings").json()["openai_key_pool_active_count"] == 0

    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value.models.list.return_value = MagicMock()
        client.post("/api/v1/system-settings/openai-key-pool",
                   json={"label": "A", "api_key": "sk-proj-aaaaaaaaaaaaaaaaaaaaa"})
        created = client.post("/api/v1/system-settings/openai-key-pool",
                              json={"label": "B", "api_key": "sk-proj-bbbbbbbbbbbbbbbbbbbbb"}).json()
    client.patch(f"/api/v1/system-settings/openai-key-pool/{created['id']}", json={"is_active": False})

    assert client.get("/api/v1/system-settings").json()["openai_key_pool_active_count"] == 1
