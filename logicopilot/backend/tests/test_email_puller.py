"""Multi-mailbox email pulling: per-operator mailbox discovery, host selection, and the
today-only date window on the IMAP search."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from app.core.email_puller import _candidate_groups, _imap_host_for, _operator_mailboxes, _pull_one_mailbox
from app.core.mail_crypto import encrypt_secret
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from app.models.user_template import UserTemplateAssignment
from tests.conftest import make_tenant, make_user


def test_imap_host_for_provider():
    assert _imap_host_for("zoho") == "imap.zoho.com"
    assert _imap_host_for("gmail") == "imap.gmail.com"
    assert _imap_host_for(None) == "imap.gmail.com"  # unset defaults to gmail, not an error


def test_operator_mailboxes_returns_only_operators_with_a_connected_inbox(db_session):
    tenant = make_tenant(db_session)
    connected = make_user(db_session, role="operator", tenant=tenant, full_name="Connected Op",
                          email="connected@example.com")
    connected.mail_provider = "zoho"
    connected.mail_email = "connected@zoho.com"
    connected.mail_app_password_encrypted = encrypt_secret("realsecret")
    not_connected = make_user(db_session, role="operator", tenant=tenant, full_name="No Mailbox",
                              email="no-mailbox@example.com")
    inactive = make_user(db_session, role="operator", tenant=tenant, full_name="Inactive",
                         email="inactive@example.com", is_active=False)
    inactive.mail_provider = "gmail"
    inactive.mail_email = "inactive@gmail.com"
    inactive.mail_app_password_encrypted = encrypt_secret("whatever")
    db_session.add_all([connected, not_connected, inactive])
    db_session.commit()

    out = _operator_mailboxes(db_session)
    assert len(out) == 1
    assert out[0]["operator_id"] == connected.id
    assert out[0]["email"] == "connected@zoho.com"
    assert out[0]["password"] == "realsecret"
    assert out[0]["host"] == "imap.zoho.com"


def test_operator_mailboxes_scopes_to_tenant(db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    op_a = make_user(db_session, role="operator", tenant=tenant_a, email="opa@example.com")
    op_a.mail_provider, op_a.mail_email = "gmail", "a@gmail.com"
    op_a.mail_app_password_encrypted = encrypt_secret("pw-a")
    op_b = make_user(db_session, role="operator", tenant=tenant_b, email="opb@example.com")
    op_b.mail_provider, op_b.mail_email = "gmail", "b@gmail.com"
    op_b.mail_app_password_encrypted = encrypt_secret("pw-b")
    db_session.add_all([op_a, op_b])
    db_session.commit()

    out = _operator_mailboxes(db_session, tenant_id=tenant_a.id)
    assert [m["email"] for m in out] == ["a@gmail.com"]


def test_operator_mailboxes_skips_undecryptable_password_without_raising(db_session, caplog):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="broken-pw@example.com")
    op.mail_provider, op.mail_email = "gmail", "broken@gmail.com"
    op.mail_app_password_encrypted = "not-actually-encrypted-garbage"
    db_session.add(op)
    db_session.commit()

    out = _operator_mailboxes(db_session)  # must not raise
    assert out == []


def _mock_conn_no_messages():
    conn = MagicMock()
    conn.search.return_value = ("OK", [b""])
    return conn


def test_pull_one_mailbox_searches_only_todays_unread_mail(db_session):
    tenant = make_tenant(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, email="puller-op@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Test Customer", status="ready",
                          pull_operator_id=operator.id)
    db_session.add(group)
    db_session.flush()
    db_session.add(CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="consignee",
        kind="hardcoded", hardcoded_value="Test Consignee Co",
    ))
    db_session.commit()

    conn = _mock_conn_no_messages()
    with patch("app.core.email_puller._connect", return_value=conn):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=operator.id)

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
    operator = make_user(db_session, role="operator", tenant=tenant, email="reexamine-op@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Test Customer 2", status="ready",
                          pull_operator_id=operator.id)
    db_session.add(group)
    db_session.flush()
    db_session.add(CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="consignee",
        kind="hardcoded", hardcoded_value="Test Consignee Co",
    ))
    db_session.commit()

    conn = _mock_conn_no_messages()
    with patch("app.core.email_puller._connect", return_value=conn):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=operator.id, reexamine=True)

    assert result["ok"] is True
    args = conn.search.call_args[0]
    assert args[1] == "ALL"


def test_pull_one_mailbox_with_no_candidate_groups_never_connects(db_session):
    tenant = make_tenant(db_session)
    with patch("app.core.email_puller._connect") as mock_connect:
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id="nobody")
    mock_connect.assert_not_called()
    assert result["ok"] is True
    assert result["processed"] == []


# --------------------------------------------------------------------------- #
# _candidate_groups — a personal mailbox is scoped by TEMPLATE ASSIGNMENTS, not
# pull_operator_id, so several operators can each have their own mailbox pulling
# into the very same template.
# --------------------------------------------------------------------------- #

def test_candidate_groups_for_operator_uses_template_assignment_not_pull_operator_id(db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="assigned-op@example.com")
    other_op = make_user(db_session, role="operator", tenant=tenant, email="other-pull-op@example.com")
    # pull_operator_id points at a COMPLETELY different operator - the old single-operator
    # field - yet `op` is still a valid candidate purely from their own assignment.
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready",
                          pull_operator_id=other_op.id)
    db_session.add(group)
    db_session.flush()
    db_session.add(UserTemplateAssignment(tenant_id=tenant.id, user_id=op.id, group_id=group.id))
    db_session.commit()

    result = _candidate_groups(db_session, tenant.id, op.id)
    assert [g.id for g in result] == [group.id]


def test_candidate_groups_lets_two_operators_share_the_same_template(db_session):
    tenant = make_tenant(db_session)
    priya = make_user(db_session, role="operator", tenant=tenant, email="priya@example.com")
    arun = make_user(db_session, role="operator", tenant=tenant, email="arun@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    db_session.add(UserTemplateAssignment(tenant_id=tenant.id, user_id=priya.id, group_id=group.id))
    db_session.add(UserTemplateAssignment(tenant_id=tenant.id, user_id=arun.id, group_id=group.id))
    db_session.commit()

    assert [g.id for g in _candidate_groups(db_session, tenant.id, priya.id)] == [group.id]
    assert [g.id for g in _candidate_groups(db_session, tenant.id, arun.id)] == [group.id]


def test_candidate_groups_with_no_assignments_sees_every_template_in_tenant(db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="unrestricted@example.com")
    g1 = TemplateGroup(tenant_id=tenant.id, name="A", status="ready")
    g2 = TemplateGroup(tenant_id=tenant.id, name="B", status="ready")
    db_session.add_all([g1, g2])
    db_session.commit()

    result = {g.id for g in _candidate_groups(db_session, tenant.id, op.id)}
    assert result == {g1.id, g2.id}


def test_candidate_groups_assignment_to_one_template_excludes_the_other(db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="restricted@example.com")
    g1 = TemplateGroup(tenant_id=tenant.id, name="Allowed", status="ready")
    g2 = TemplateGroup(tenant_id=tenant.id, name="Not allowed", status="ready")
    db_session.add_all([g1, g2])
    db_session.flush()
    db_session.add(UserTemplateAssignment(tenant_id=tenant.id, user_id=op.id, group_id=g1.id))
    db_session.commit()

    result = [g.id for g in _candidate_groups(db_session, tenant.id, op.id)]
    assert result == [g1.id]
