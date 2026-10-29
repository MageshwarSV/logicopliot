from datetime import datetime, timedelta, timezone

from app.api.v1.auth import _ROTATION_GRACE_SECONDS
from app.core.config import get_settings
from app.models.refresh_token import RefreshToken
from app.models.user import OPERATOR, SUPER_ADMIN
from tests.conftest import DEFAULT_PASSWORD, login, make_tenant, make_user


def _age_revoked_tokens_past_the_grace_window(db_session) -> None:
    """Make every already-revoked token look rotated LONG ago, so a replay of one reads as a
    genuine replay rather than as two tabs refreshing at the same moment (see
    _ROTATION_GRACE_SECONDS in app/api/v1/auth.py)."""
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=_ROTATION_GRACE_SECONDS + 5)
    for row in db_session.query(RefreshToken).filter(RefreshToken.revoked_at.isnot(None)).all():
        row.revoked_at = long_ago
        db_session.add(row)
    db_session.commit()


def test_login_success(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = login(client, "sa@example.com")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "sa@example.com"
    assert "access_token" in response.cookies
    assert "refresh_token" in response.cookies


def test_login_response_carries_the_users_own_assigned_modes(client, db_session):
    # UserOut.model_validate(user) alone never populates `modes` (renamed from the model's own
    # assigned_modes column) - found live: a filter comparing "my own modes" against another
    # user's real modes always compared against an empty set on my own side, because the
    # login/me/refresh responses were built this bare way instead of through _user_out, which
    # the GET /users list endpoint already used correctly.
    tenant = make_tenant(db_session)
    user = make_user(db_session, role=OPERATOR, tenant=tenant, email="modes-op@example.com")
    user.assigned_modes = ["Sea Import"]
    db_session.add(user)
    db_session.commit()

    response = login(client, "modes-op@example.com")
    assert response.status_code == 200
    assert response.json()["user"]["modes"] == ["Sea Import"]

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["modes"] == ["Sea Import"]


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
    _age_revoked_tokens_past_the_grace_window(db_session)
    client.cookies.set("refresh_token", old_refresh_cookie)
    replay_response = client.post("/api/v1/auth/refresh")
    assert replay_response.status_code == 401

    # The legitimate rotated token should also now be dead (family revoked).
    client.cookies.set("refresh_token", new_refresh_cookie)
    after_theft_response = client.post("/api/v1/auth/refresh")
    assert after_theft_response.status_code == 401


def test_two_tabs_refreshing_at_the_same_moment_does_not_kill_the_session(client, db_session):
    """This app opens each job in its OWN TAB, so an operator normally has several open at
    once. They share one cookie jar, so when the access token expires they all notice together
    and each posts /auth/refresh with the SAME refresh token: the first rotates it, the rest
    land milliseconds later to find it revoked. Reading that as theft revoked every session the
    user had - 11 tokens killed in one instant on 15 Oct in the UAT table, 10 on 13 Oct - and
    threw the operator out of every tab mid-job."""
    make_user(db_session, role=SUPER_ADMIN, email="sa-tabs@example.com")
    login(client, "sa-tabs@example.com")
    in_flight_cookie = client.cookies.get("refresh_token")

    first_tab = client.post("/api/v1/auth/refresh")
    assert first_tab.status_code == 200
    rotated_cookie = client.cookies.get("refresh_token")

    # The second tab's request was already in flight carrying the pre-rotation cookie.
    client.cookies.set("refresh_token", in_flight_cookie)
    second_tab = client.post("/api/v1/auth/refresh")
    assert second_tab.status_code == 200

    # ...and the first tab's own freshly-rotated token is still alive: nothing was revoked
    # behind its back, which is the part that used to log every other tab out.
    client.cookies.set("refresh_token", rotated_cookie)
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_the_access_token_expires_first_so_the_idle_window_can_actually_slide(client, db_session):
    """The inactivity timeout is the REFRESH token's lifetime, and it only resets when a
    rotation happens. A rotation only happens when the ACCESS token has expired and the
    interceptor calls /auth/refresh - so if both expired together there would never be a live
    refresh token left to rotate with, and "60 minutes idle" would silently become a hard
    60-minute cap from login. That is exactly what shipped on 22-23 Oct: rotations across the
    whole UAT database fell to ZERO and operators were logged out on the hour, every hour.
    The ordering, not either number on its own, is what makes the window slide."""
    settings = get_settings()
    assert settings.refresh_token_expire_minutes == 60
    assert settings.access_token_expire_minutes < settings.refresh_token_expire_minutes

    make_user(db_session, role=SUPER_ADMIN, email="sa-timeout@example.com")
    response = login(client, "sa-timeout@example.com")
    assert response.status_code == 200

    # A config value nothing reads from would pass the assertions above while the real cookies
    # stayed on the old numbers, so check what the browser is actually handed.
    set_cookie_headers = response.headers.get_list("set-cookie")
    access_header = next(h for h in set_cookie_headers if h.startswith("access_token="))
    refresh_header = next(h for h in set_cookie_headers if h.startswith("refresh_token="))
    assert f"Max-Age={settings.access_token_expire_minutes * 60}" in access_header
    assert "Max-Age=3600" in refresh_header


def test_logout_invalidates_session(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 401

    refresh_response = client.post("/api/v1/auth/refresh")
    assert refresh_response.status_code == 401
