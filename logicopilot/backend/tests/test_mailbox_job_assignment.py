"""Every new job starts Unassigned, even one pulled from an operator's own connected
mailbox - assignment is now always a deliberate action from the Jobs list' own dropdown,
never an automatic side effect of whose mailbox it arrived in or the template's
pull_operator_id."""

import email
import io
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.email_puller import _pull_one_mailbox
from app.models.custom_field import CustomField
from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _fake_message(message_id: str) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "customer@example.com"
    msg["Subject"] = "Shipment docs"
    msg.set_content("Please find attached.")
    msg.add_attachment(_pdf_bytes(), maintype="application", subtype="pdf", filename="bl.pdf")
    return msg.as_bytes()


def _mock_conn_with_one_message(raw_bytes: bytes):
    conn = MagicMock()
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1 (BODY.PEEK[] {n}", raw_bytes)])
    return conn


def test_job_from_personal_mailbox_starts_unassigned_regardless_of_pull_operator_id(db_session):
    tenant = make_tenant(db_session)
    mailbox_owner = make_user(db_session, role="operator", tenant=tenant, email="owner@example.com")
    someone_else = make_user(db_session, role="operator", tenant=tenant, email="else@example.com")

    # The template's pull_operator_id names a different operator entirely - it no longer
    # matters, since every new job starts unassigned either way.
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready",
                          pull_operator_id=someone_else.id)
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()

    raw = _fake_message("<abc123@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               return_value={"keys": [group.id], "reason": "matched", "evidence": "Test Consignee Co"}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=mailbox_owner.id,
                                   mail_email=mailbox_owner.mail_email or "owner@zoho.com",
                                   mail_app_password="whatever", mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    jobs = db_session.query(Job).filter(Job.group_id == group.id).all()
    assert len(jobs) == 1
    assert jobs[0].assigned_operator_id is None

    # A successfully processed message is left UNREAD on purpose (removes \Seen, never
    # adds it) - re-processing is prevented separately, by EmailSeen, not by this flag.
    conn.store.assert_called_once_with(b"1", "-FLAGS", "\\Seen")


def test_single_template_mailbox_routes_without_any_customer_identifier_configured(db_session):
    # The real case that prompted this: an operator's mailbox connected to exactly ONE
    # template, with NO consignee/IEC/GSTIN configured on it at all - previously this
    # would have been rejected outright ("no candidates"). A dedicated single-template
    # mailbox has nowhere else the mail could belong, so it must still route successfully.
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="solo-owner@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    # Deliberately NO CustomField / no identifying field mark example anywhere.
    db_session.commit()

    raw = _fake_message("<solo-route@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some shipment text", "tokens": []}), \
         patch("app.core.classifier.identify_customer") as mock_identify, \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="solo-owner@zoho.com", mail_app_password="whatever",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    # identify_customer was never even called - there was nothing to disambiguate.
    mock_identify.assert_not_called()
    jobs = db_session.query(Job).filter(Job.group_id == group.id).all()
    assert len(jobs) == 1
    assert jobs[0].assigned_operator_id is None


def test_operator_with_two_templates_still_requires_content_identification(db_session):
    # The safety behaviour this must NOT remove: an operator connected to more than one
    # template still needs the documents to say which one they belong to - there IS
    # ambiguity to resolve here, unlike the single-template case above.
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="multi-owner@example.com")
    group_a = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    group_b = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="ready")
    db_session.add_all([group_a, group_b])
    db_session.flush()
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group_a.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Consignee A"))
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group_b.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Consignee B"))
    db_session.commit()

    raw = _fake_message("<multi-route@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               return_value={"keys": [], "reason": "no match", "evidence": ""}) as mock_identify, \
         patch("app.core.classifier.assign_documents_detailed", return_value=[[]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="multi-owner@zoho.com", mail_app_password="whatever",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    mock_identify.assert_called_once()
    assert db_session.query(Job).filter(Job.group_id.in_([group_a.id, group_b.id])).count() == 0
