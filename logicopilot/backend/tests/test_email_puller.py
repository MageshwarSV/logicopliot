"""Multi-mailbox email pulling: Connected Mailbox discovery, host selection, and the
today-only date window on the IMAP search.

Mailboxes are their own thing now (ConnectedMailbox), independent of any operator user - see
app/models/connected_mailbox.py's own docstring. A mailbox's tenant_id/group_id (set from the
Email Puller settings screen) replace what used to be an operator's own UserTemplateAssignment
scoping (see test_connected_mailboxes.py for the assignment-flow tests themselves)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.core.email_puller import _candidate_groups, _connected_mailboxes, _imap_host_for, _pull_one_mailbox
from app.core.mail_crypto import encrypt_secret
from app.models.connected_mailbox import ConnectedMailbox
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_mailbox(db_session, tenant=None, group=None, email="connected@zoho.com",
                  provider="zoho", password="realsecret", paused=False):
    mb = ConnectedMailbox(
        provider=provider, email=email, host=_imap_host_for(provider),
        app_password_encrypted=encrypt_secret(password),
        tenant_id=tenant.id if tenant else None,
        group_id=group.id if group else None,
        paused=paused,
    )
    db_session.add(mb)
    db_session.commit()
    db_session.refresh(mb)
    return mb


def test_imap_host_for_provider():
    assert _imap_host_for("zoho") == "imap.zoho.com"
    assert _imap_host_for("gmail") == "imap.gmail.com"
    assert _imap_host_for(None) == "imap.gmail.com"  # unset defaults to gmail, not an error


def test_connected_mailboxes_excludes_unassigned_ones(db_session):
    tenant = make_tenant(db_session)
    _make_mailbox(db_session, tenant=tenant, email="assigned@zoho.com")
    _make_mailbox(db_session, tenant=None, email="unassigned@zoho.com")  # no tenant yet

    out = _connected_mailboxes(db_session)
    emails = {m["email"] for m in out}
    assert "assigned@zoho.com" in emails
    assert "unassigned@zoho.com" not in emails


def test_connected_mailboxes_excludes_a_paused_one(db_session):
    tenant = make_tenant(db_session)
    _make_mailbox(db_session, tenant=tenant, email="active@zoho.com")
    _make_mailbox(db_session, tenant=tenant, email="paused@zoho.com", paused=True)

    out = _connected_mailboxes(db_session)
    emails = {m["email"] for m in out}
    assert "active@zoho.com" in emails
    assert "paused@zoho.com" not in emails


def test_connected_mailboxes_returns_decrypted_password_and_host(db_session):
    tenant = make_tenant(db_session)
    _make_mailbox(db_session, tenant=tenant, email="connected@zoho.com", provider="zoho",
                  password="realsecret")

    out = _connected_mailboxes(db_session)
    assert len(out) == 1
    assert out[0]["email"] == "connected@zoho.com"
    assert out[0]["password"] == "realsecret"
    assert out[0]["host"] == "imap.zoho.com"
    assert out[0]["tenant_id"] == tenant.id
    assert out[0]["group_id"] is None


def test_connected_mailboxes_scopes_to_tenant(db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    _make_mailbox(db_session, tenant=tenant_a, email="a@gmail.com", provider="gmail")
    _make_mailbox(db_session, tenant=tenant_b, email="b@gmail.com", provider="gmail")

    out = _connected_mailboxes(db_session, tenant_id=tenant_a.id)
    assert [m["email"] for m in out] == ["a@gmail.com"]


def test_connected_mailboxes_skips_undecryptable_password_without_raising(db_session):
    tenant = make_tenant(db_session)
    mb = ConnectedMailbox(
        provider="gmail", email="broken@gmail.com", host="imap.gmail.com",
        app_password_encrypted="not-actually-encrypted-garbage", tenant_id=tenant.id,
    )
    db_session.add(mb)
    db_session.commit()

    out = _connected_mailboxes(db_session)  # must not raise
    assert out == []


def _mock_conn_no_messages():
    conn = MagicMock()
    conn.search.return_value = ("OK", [b""])
    return conn


def test_pull_one_mailbox_searches_only_todays_unread_mail(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Test Customer", status="ready")
    db_session.add(group)
    db_session.flush()
    db_session.add(CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="consignee",
        kind="hardcoded", hardcoded_value="Test Consignee Co",
    ))
    db_session.commit()

    conn = _mock_conn_no_messages()
    with patch("app.core.email_puller._connect", return_value=conn):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id)

    assert result["ok"] is True
    conn.select.assert_called_once_with("INBOX")
    assert conn.search.call_count == 1
    args = conn.search.call_args[0]
    assert args[0] is None
    assert args[1] == "UNSEEN"
    assert args[2] == "SINCE"
    assert args[4] == "BEFORE"
    today_str = datetime.now().strftime("%d-%b-%Y")
    assert args[3] == today_str
    conn.logout.assert_called_once()


def test_reexamine_searches_all_mail_not_just_unread(db_session):
    # reexamine means "look again at something already handled" - a message that
    # succeeded is deliberately left read (or a human read it), so UNSEEN would silently
    # exclude the very message reexamine is meant to reach. Confirms the search widens to
    # ALL, still within today's date window, when reexamine=True.
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Test Customer 2", status="ready")
    db_session.add(group)
    db_session.flush()
    db_session.add(CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="consignee",
        kind="hardcoded", hardcoded_value="Test Consignee Co",
    ))
    db_session.commit()

    conn = _mock_conn_no_messages()
    with patch("app.core.email_puller._connect", return_value=conn):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   reexamine=True)

    assert result["ok"] is True
    args = conn.search.call_args[0]
    assert args[1] == "ALL"


def test_pull_one_mailbox_with_no_candidate_groups_never_connects(db_session):
    tenant = make_tenant(db_session)
    with patch("app.core.email_puller._connect") as mock_connect:
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id)
    mock_connect.assert_not_called()
    assert result["ok"] is True
    assert result["processed"] == []


# --------------------------------------------------------------------------- #
# _candidate_groups — tenant filtering only now; a mailbox's own group_id (forced_group_id,
# see _pull_one_mailbox above) is what used to be operator-scoped assignment.
# --------------------------------------------------------------------------- #

def test_candidate_groups_scopes_to_tenant(db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    g1 = TemplateGroup(tenant_id=tenant_a.id, name="A", status="ready")
    g2 = TemplateGroup(tenant_id=tenant_b.id, name="B", status="ready")
    db_session.add_all([g1, g2])
    db_session.commit()

    assert [g.id for g in _candidate_groups(db_session, tenant_a.id)] == [g1.id]


def test_candidate_groups_with_no_tenant_sees_every_template(db_session):
    tenant = make_tenant(db_session)
    g1 = TemplateGroup(tenant_id=tenant.id, name="A", status="ready")
    g2 = TemplateGroup(tenant_id=tenant.id, name="B", status="ready")
    db_session.add_all([g1, g2])
    db_session.commit()

    result = {g.id for g in _candidate_groups(db_session, None)}
    assert {g1.id, g2.id} <= result
