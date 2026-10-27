"""The native Manager app's way in: a Bearer token instead of a cookie.

React Native has no cookie jar that survives the app being closed, so the app asks for its
tokens in the login response body and presents them as `Authorization: Bearer`. Everything
here is additive - the browser never sets `token_response`, never sends the header, and its
httpOnly cookies keep working exactly as before. These tests exist to keep that true: the
web's behaviour is asserted alongside the app's in almost every one of them.
"""
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import DEFAULT_PASSWORD, login, make_tenant, make_user


def _login_as_app(client, email: str, password: str = DEFAULT_PASSWORD):
    """What the mobile app posts: the same credentials, plus the opt-in flag."""
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "token_response": True},
    )


def test_web_login_still_returns_no_tokens_in_the_body(client, db_session):
    """The whole safety argument for this feature: a browser's tokens stay httpOnly, i.e.
    unreadable from JavaScript. If this ever starts failing, the change has leaked tokens
    into the body for every existing user, which is strictly worse than before it existed."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = login(client, "sa@example.com")

    assert response.status_code == 200
    assert response.json()["tokens"] is None
    # ...while the cookies it has always set are still set.
    assert "access_token" in response.cookies
    assert "refresh_token" in response.cookies


def test_app_login_returns_a_usable_token_pair(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    response = _login_as_app(client, "sa@example.com")

    assert response.status_code == 200
    tokens = response.json()["tokens"]
    assert tokens is not None
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    # Seconds, and both non-zero - the app counts down from these.
    assert tokens["expires_in"] > 0
    assert tokens["refresh_expires_in"] > 0


def test_bearer_token_authenticates_with_no_cookie_at_all(client, db_session):
    """The real test: clear every cookie, as a freshly-restarted phone would have, and prove
    the token alone still gets in."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tokens = _login_as_app(client, "sa@example.com").json()["tokens"]

    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401  # nothing to go on

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "sa@example.com"


def test_bearer_token_carries_the_users_role_and_tenant(client, db_session):
    """A Bearer session is the SAME session, not a weaker one - same user, same role, same
    tenant. The app being read-only is enforced by the role, so this must not drift."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    tokens = _login_as_app(client, "ta@example.com").json()["tokens"]

    client.cookies.clear()
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    ).json()
    assert me["role"] == TENANT_ADMIN
    assert me["tenant_id"] == tenant.id


def test_malformed_authorization_headers_are_rejected_as_unauthenticated(client, db_session):
    """Not 500, and not accidentally authenticated. Each of these is a shape the header can
    arrive in from a buggy client."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    client.cookies.clear()

    for header in (
        "Bearer",                 # scheme only, no token
        "Bearer    ",             # scheme and whitespace
        "Basic abc123",           # wrong scheme
        "abc123",                 # no scheme
        "Bearer not-a-jwt",       # right shape, rubbish token
    ):
        response = client.get("/api/v1/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, f"{header!r} should not authenticate"


def test_app_can_refresh_with_the_token_in_the_body(client, db_session):
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tokens = _login_as_app(client, "sa@example.com").json()["tokens"]

    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"], "token_response": True},
    )
    assert response.status_code == 200
    new_tokens = response.json()["tokens"]
    assert new_tokens is not None
    # Rotation still happens for the app, exactly as it does for the browser.
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    client.cookies.clear()
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert me.status_code == 200


def test_a_replayed_app_refresh_token_is_rejected_like_any_other(client, db_session):
    """Reuse detection is not something the app opts out of. The second use of a rotated
    token is theft on a phone for the same reason it is in a browser."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tokens = _login_as_app(client, "sa@example.com").json()["tokens"]
    client.cookies.clear()

    first = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"], "token_response": True},
    )
    assert first.status_code == 200

    # A successful refresh still SETS cookies, and the test client keeps them even though a
    # phone would not. Without this clear, the replay below would be served from that fresh
    # cookie (which the endpoint reads before the body, on purpose - see refresh) and would
    # succeed, hiding the very thing this test exists to check.
    client.cookies.clear()

    replay = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"], "token_response": True},
    )
    assert replay.status_code == 401


def test_app_logout_revokes_the_session_server_side(client, db_session):
    """Without a body-carried token the app could only forget its token locally, leaving the
    refresh token live until it expired on its own."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    tokens = _login_as_app(client, "sa@example.com").json()["tokens"]
    client.cookies.clear()

    assert client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    ).status_code == 204

    client.cookies.clear()
    refused = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"], "token_response": True},
    )
    assert refused.status_code == 401


def test_web_refresh_and_logout_still_work_with_no_body(client, db_session):
    """The browser posts these with no body at all. Making the body optional is what keeps
    that working - a required model here would have 422'd every web session."""
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["tokens"] is None

    assert client.post("/api/v1/auth/logout").status_code == 204


def test_cookie_still_wins_when_both_are_present(client, db_session):
    """Order matters: the cookie is read first, so a browser that somehow also carries an
    Authorization header behaves exactly as it did before this change."""
    alice = make_user(db_session, role=SUPER_ADMIN, email="alice@example.com")
    make_user(db_session, role=SUPER_ADMIN, email="bob@example.com")

    bob_tokens = _login_as_app(client, "bob@example.com").json()["tokens"]
    client.cookies.clear()
    login(client, "alice@example.com")  # alice's cookie is now on the client

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bob_tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == alice.email
