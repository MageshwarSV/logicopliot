"""Operator-owned mailbox: connect/verify/disconnect through the Users API, and the
lower-level pieces (encryption round-trip, mailbox discovery for the poller)."""

from unittest.mock import MagicMock, patch

import pytest

from app.core.mail_crypto import decrypt_secret, encrypt_secret
from app.models.user import User
from tests.conftest import login, make_tenant, make_user


def _imap_ok():
    """A mock imaplib.IMAP4_SSL(...) whose login()/logout() both succeed."""
    conn = MagicMock()
    conn.login.return_value = ("OK", [b"success"])
    return conn


def _imap_fails():
    conn = MagicMock()
    conn.login.side_effect = Exception("AUTHENTICATIONFAILED")
    return conn


# --------------------------------------------------------------------------- #
# mail_crypto — pure round-trip, no network
# --------------------------------------------------------------------------- #

def test_encrypt_decrypt_round_trip():
    secret = "abcd efgh ijkl mnop"
    enc = encrypt_secret(secret)
    assert enc != secret
    assert decrypt_secret(enc) == secret


def test_encrypted_value_is_not_the_plaintext_substring():
    secret = "supersecretapppassword"
    enc = encrypt_secret(secret)
    assert secret not in enc


# --------------------------------------------------------------------------- #
# create_user with a mailbox
# --------------------------------------------------------------------------- #

def test_create_operator_with_valid_mailbox_connects_and_hides_password(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", return_value=_imap_ok()):
        resp = client.post("/api/v1/users", json={
            "email": "op1@example.com", "password": "TestPass123", "full_name": "Op One",
            "role": "operator",
            "mail_provider": "gmail", "mail_email": "op1@gmail.com", "mail_app_password": "abcd efgh ijkl mnop",
        })

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mail_connected"] is True
    assert body["mail_provider"] == "gmail"
    assert body["mail_email"] == "op1@gmail.com"
    assert "mail_app_password" not in body
    assert "mail_app_password_encrypted" not in body

    # And the password really is encrypted at rest, not stored in the clear.
    stored = db_session.query(User).filter(User.email == "op1@example.com").one()
    assert stored.mail_app_password_encrypted is not None
    assert "abcd efgh ijkl mnop" not in stored.mail_app_password_encrypted
    assert decrypt_secret(stored.mail_app_password_encrypted) == "abcdefghijklmnop"  # spaces stripped


def test_create_operator_with_bad_app_password_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta2@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", return_value=_imap_fails()):
        resp = client.post("/api/v1/users", json={
            "email": "op2@example.com", "password": "TestPass123", "full_name": "Op Two",
            "role": "operator",
            "mail_provider": "gmail", "mail_email": "op2@gmail.com", "mail_app_password": "wrongpassword",
        })

    assert resp.status_code == 400
    assert "Could not sign in" in resp.json()["detail"]
    # And no half-created user was left behind.
    assert db_session.query(User).filter(User.email == "op2@example.com").first() is None


@pytest.mark.parametrize("partial", [
    {"mail_email": "x@gmail.com"},          # password missing
    {"mail_app_password": "abcd"},          # email missing
    {"mail_provider": "gmail", "mail_email": "x@gmail.com"},  # password missing, provider alone is not enough
])
def test_create_operator_with_email_or_password_alone_is_rejected(client, db_session, partial):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta3@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/users", json={
        "email": "op3@example.com", "password": "TestPass123", "full_name": "Op Three",
        "role": "operator", **partial,
    })
    assert resp.status_code == 400
    assert "both required" in resp.json()["detail"]


def test_create_operator_with_only_provider_and_no_credentials_is_a_harmless_noop(client, db_session):
    # A provider alone (no email/password) connects nothing - it is simply ignored, not an error.
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta3b@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/users", json={
        "email": "op3b@example.com", "password": "TestPass123", "full_name": "Op Three B",
        "role": "operator", "mail_provider": "gmail",
    })
    assert resp.status_code == 201
    assert resp.json()["mail_connected"] is False


def test_create_operator_with_bad_provider_name_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta4@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/users", json={
        "email": "op4@example.com", "password": "TestPass123", "full_name": "Op Four",
        "role": "operator",
        "mail_provider": "outlook", "mail_email": "op4@outlook.com", "mail_app_password": "abcd",
    })
    assert resp.status_code == 422  # pydantic validation, before it ever reaches the endpoint


# --------------------------------------------------------------------------- #
# Provider auto-detection — no provider given, so both hosts are tried with the
# same credentials and whichever one logs in successfully wins.
# --------------------------------------------------------------------------- #

def _imap_by_host(working_host: str):
    """A imaplib.IMAP4_SSL(host) stand-in: succeeds only for the given host, fails for any
    other - so the caller can tell which host the code actually tried."""
    def factory(host, *a, **kw):
        conn = MagicMock()
        if host == working_host:
            conn.login.return_value = ("OK", [b"success"])
        else:
            conn.login.side_effect = Exception("AUTHENTICATIONFAILED")
        return conn
    return factory


def test_create_operator_autodetects_gmail_without_provider(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="tad1@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", side_effect=_imap_by_host("imap.gmail.com")):
        resp = client.post("/api/v1/users", json={
            "email": "opd1@example.com", "password": "TestPass123", "full_name": "Op D1",
            "role": "operator",
            "mail_email": "ops@4slogistics.com", "mail_app_password": "abcd1234",
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["mail_provider"] == "gmail"
    assert resp.json()["mail_connected"] is True


def test_create_operator_autodetects_zoho_when_gmail_fails(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="tad2@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", side_effect=_imap_by_host("imap.zoho.com")):
        resp = client.post("/api/v1/users", json={
            "email": "opd2@example.com", "password": "TestPass123", "full_name": "Op D2",
            "role": "operator",
            # Same-looking custom domain, this time actually hosted on Zoho Mail.
            "mail_email": "ops@4slogistics.com", "mail_app_password": "abcd1234",
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["mail_provider"] == "zoho"
    assert resp.json()["mail_connected"] is True


def test_create_operator_finds_india_region_when_global_zoho_host_fails(client, db_session):
    # The exact real-world case: a Zoho Mail account hosted on the India data center
    # rejects imap.zoho.com with "Invalid credentials" (indistinguishable from a genuinely
    # wrong password) even though the credentials are correct - only imap.zoho.in accepts
    # them. Detection must keep going past the global host and find the real one.
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="tad2b@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", side_effect=_imap_by_host("imap.zoho.in")):
        resp = client.post("/api/v1/users", json={
            "email": "opd2b@example.com", "password": "TestPass123", "full_name": "Op D2B",
            "role": "operator",
            "mail_email": "mageshwar.sv@workboosterai.com", "mail_app_password": "abcd1234",
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["mail_provider"] == "zoho"
    assert resp.json()["mail_connected"] is True


def test_explicit_zoho_provider_also_falls_back_through_regions(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="tad2c@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", side_effect=_imap_by_host("imap.zoho.eu")):
        resp = client.post("/api/v1/users", json={
            "email": "opd2c@example.com", "password": "TestPass123", "full_name": "Op D2C",
            "role": "operator", "mail_provider": "zoho",
            "mail_email": "ops@example.co.uk", "mail_app_password": "abcd1234",
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["mail_provider"] == "zoho"


def test_create_operator_autodetect_fails_on_both_names_both_in_error(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="tad3@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", side_effect=_imap_by_host("neither")):
        resp = client.post("/api/v1/users", json={
            "email": "opd3@example.com", "password": "TestPass123", "full_name": "Op D3",
            "role": "operator",
            "mail_email": "ops@4slogistics.com", "mail_app_password": "wrongpassword",
        })
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "gmail" in detail and "zoho" in detail
    assert db_session.query(User).filter(User.email == "opd3@example.com").first() is None


def test_create_operator_with_no_mailbox_fields_still_works(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta5@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/users", json={
        "email": "op5@example.com", "password": "TestPass123", "full_name": "Op Five", "role": "operator",
    })
    assert resp.status_code == 201
    assert resp.json()["mail_connected"] is False


# --------------------------------------------------------------------------- #
# update_user — connect, change, and disconnect
# --------------------------------------------------------------------------- #

def test_update_user_can_connect_a_mailbox_after_creation(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta6@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op6@example.com")
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", return_value=_imap_ok()):
        resp = client.patch(f"/api/v1/users/{op.id}", json={
            "mail_provider": "zoho", "mail_email": "op6@zoho.com", "mail_app_password": "abcd1234",
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["mail_connected"] is True
    assert resp.json()["mail_provider"] == "zoho"


def test_update_user_disconnect_clears_all_three_fields(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta7@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op7@example.com")
    op.mail_provider = "gmail"
    op.mail_email = "op7@gmail.com"
    op.mail_app_password_encrypted = encrypt_secret("whatever")
    db_session.add(op)
    db_session.commit()
    login(client, admin.email)

    resp = client.patch(f"/api/v1/users/{op.id}", json={"mail_app_password": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mail_connected"] is False
    assert body["mail_provider"] is None
    assert body["mail_email"] is None

    db_session.refresh(op)
    assert op.mail_app_password_encrypted is None


def test_update_user_changing_email_without_new_password_reverifies_old_one(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta8@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op8@example.com")
    op.mail_provider = "gmail"
    op.mail_email = "old@gmail.com"
    op.mail_app_password_encrypted = encrypt_secret("realapppassword")
    db_session.add(op)
    db_session.commit()
    login(client, admin.email)

    with patch("imaplib.IMAP4_SSL", return_value=_imap_ok()) as mock_ssl:
        resp = client.patch(f"/api/v1/users/{op.id}", json={"mail_email": "new@gmail.com"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mail_email"] == "new@gmail.com"
    # It really did re-verify with the DECRYPTED existing password, against the new address.
    conn = mock_ssl.return_value
    conn.login.assert_called_once_with("new@gmail.com", "realapppassword")


def test_update_user_email_change_with_no_password_on_file_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta9@example.com")
    op = make_user(db_session, role="operator", tenant=tenant, email="op9@example.com")
    login(client, admin.email)

    resp = client.patch(f"/api/v1/users/{op.id}", json={"mail_email": "new@gmail.com"})
    assert resp.status_code == 400
