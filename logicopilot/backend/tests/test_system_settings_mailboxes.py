"""Per-mailbox pause (User.mail_paused) - lets a Super Admin stop ONE operator's noisy or
misconfigured mailbox from the Settings page without touching the system-wide email_pull_paused
switch (test_system_settings_pause.py) or any other operator's inbox."""
from app.core.email_puller import _operator_mailboxes
from tests.conftest import login, make_tenant, make_user


def _connect_mailbox(db_session, user, email="op@example.com", provider="zoho"):
    user.mail_provider = provider
    user.mail_email = email
    user.mail_app_password_encrypted = "not-a-real-secret"
    db_session.commit()
    db_session.refresh(user)
    return user


def test_operator_mailboxes_excludes_a_paused_one(db_session):
    tenant = make_tenant(db_session)
    active_op = make_user(db_session, role="operator", tenant=tenant, email="active-op@example.com")
    _connect_mailbox(db_session, active_op, email="active-op-mail@example.com")

    paused_op = make_user(db_session, role="operator", tenant=tenant, email="paused-op@example.com")
    _connect_mailbox(db_session, paused_op, email="paused-op-mail@example.com")
    paused_op.mail_paused = True
    db_session.commit()

    from unittest.mock import patch
    # decrypt_secret is imported inline inside _operator_mailboxes - patch its real home so
    # that late import picks up the stub instead of trying to decrypt the fake secret above.
    with patch("app.core.mail_crypto.decrypt_secret", side_effect=lambda v: v):
        boxes = _operator_mailboxes(db_session)

    emails = {b["email"] for b in boxes}
    assert "active-op-mail@example.com" in emails
    assert "paused-op-mail@example.com" not in emails


def test_list_mailboxes_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-mbx@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/system-settings/mailboxes")
    assert resp.status_code == 403


def test_list_mailboxes_returns_every_connected_operator_across_tenants(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    op_a = make_user(db_session, role="operator", tenant=tenant_a, email="opa@example.com", full_name="Op A")
    _connect_mailbox(db_session, op_a, email="opa-mail@example.com", provider="gmail")
    op_b = make_user(db_session, role="operator", tenant=tenant_b, email="opb@example.com", full_name="Op B")
    _connect_mailbox(db_session, op_b, email="opb-mail@example.com", provider="zoho")
    # An operator with NO mailbox connected must not appear at all.
    make_user(db_session, role="operator", tenant=tenant_a, email="op-none@example.com")

    admin = make_user(db_session, role="super_admin", email="sa-mbx1@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/system-settings/mailboxes")
    assert resp.status_code == 200
    rows = resp.json()
    emails = {r["mail_email"] for r in rows}
    assert emails == {"opa-mail@example.com", "opb-mail@example.com"}
    by_email = {r["mail_email"]: r for r in rows}
    assert by_email["opa-mail@example.com"]["mail_provider"] == "gmail"
    assert by_email["opa-mail@example.com"]["tenant_name"] == "Tenant A"
    assert by_email["opa-mail@example.com"]["mail_paused"] is False


def test_pausing_one_mailbox_via_api_does_not_affect_another(client, db_session):
    tenant = make_tenant(db_session)
    op1 = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    _connect_mailbox(db_session, op1, email="op1-mail@example.com")
    op2 = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    _connect_mailbox(db_session, op2, email="op2-mail@example.com")

    admin = make_user(db_session, role="super_admin", email="sa-mbx2@example.com")
    login(client, admin.email)
    resp = client.post(f"/api/v1/system-settings/mailboxes/{op1.id}/pause", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json() == {"user_id": op1.id, "mail_paused": True}

    resp = client.get("/api/v1/system-settings/mailboxes")
    rows = {r["mail_email"]: r["mail_paused"] for r in resp.json()}
    assert rows["op1-mail@example.com"] is True
    assert rows["op2-mail@example.com"] is False


def test_pausing_a_nonexistent_mailbox_404s(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-mbx3@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/mailboxes/does-not-exist/pause", json={"paused": True})
    assert resp.status_code == 404


def test_pausing_an_operator_with_no_mailbox_404s(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="no-mail-op@example.com")

    admin = make_user(db_session, role="super_admin", email="sa-mbx4@example.com")
    login(client, admin.email)
    resp = client.post(f"/api/v1/system-settings/mailboxes/{op.id}/pause", json={"paused": True})
    assert resp.status_code == 404
