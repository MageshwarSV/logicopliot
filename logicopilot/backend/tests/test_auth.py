from app.models.user import SUPER_ADMIN
from tests.conftest import DEFAULT_PASSWORD, login, make_tenant, make_user


def test_login_success(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = login(client, "sa@example.com")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "sa@example.com"
    assert "access_token" in response.cookies
    assert "refresh_token" in response.cookies


def test_login_wrong_password(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = login(client, "sa@example.com", password="wrong-password")
    assert response.status_code == 401


def test_login_unknown_email(client, db_session):
    response = login(client, "nobody@example.com")
    assert response.status_code == 401


def test_login_inactive_user_rejected(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="disabled@example.com", is_active=False)
    response = login(client, "disabled@example.com")
    assert response.status_code == 401


def test_me_requires_auth(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "sa@example.com"


def test_refresh_rotates_token_and_replayed_old_token_is_rejected(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    old_refresh_cookie = client.cookies.get("refresh_token")

    refresh_response = client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 200
    new_refresh_cookie = client.cookies.get("refresh_token")
    assert new_refresh_cookie != old_refresh_cookie

    # Replay the OLD refresh token (simulating a stolen/reused token) — must be rejected,
    # and per the theft-detection design, the whole session family is now dead too.
    client.cookies.set("refresh_token", old_refresh_cookie)
    replay_response = client.post("/api/v1/auth/refresh")
    assert replay_response.status_code == 401

    # The legitimate rotated token should also now be dead (family revoked).
    client.cookies.set("refresh_token", new_refresh_cookie)
    after_theft_response = client.post("/api/v1/auth/refresh")
    assert after_theft_response.status_code == 401


def test_logout_invalidates_session(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 401

    refresh_response = client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 401
