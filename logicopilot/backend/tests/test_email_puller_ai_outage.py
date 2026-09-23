"""When the AI service itself fails (OpenAI quota exhausted, rate limited, connection error,
5xx) mid-message, that message must NOT be recorded as "no customer identified" or "no
document matched" - those are permanent verdicts that block every future ordinary poll from
trying again, and a quota outage has nothing to do with whether the message actually matches a
customer. It must come back exactly as if never touched: no EmailSeen row at all, so the very
next poll (not just reexamine) claims and retries it fresh. See AIServiceUnavailable and
_unclaim in app/core/email_puller.py."""

import io
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.classifier import AIServiceUnavailable
from app.core.email_puller import _pull_one_mailbox
from app.models.custom_field import CustomField
from app.models.email_seen import EmailSeen
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


def _setup(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    return group, doc


def test_identify_customer_outage_leaves_the_message_completely_unclaimed(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op@example.com")
    group, doc = _setup(db_session, tenant)
    # A second template so identify_customer is actually consulted (a single-candidate
    # mailbox bypasses it entirely - not the scenario being tested here).
    db_session.add(TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready"))
    db_session.commit()

    raw = _fake_message("<outage-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               side_effect=AIServiceUnavailable("insufficient_quota")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="outage-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert result["processed"][0]["deferred"] is True

    # No job was created, no EmailSeen row was left behind, and the IMAP flag was never
    # touched - this message is EXACTLY as untouched as before this poll ran.
    assert db_session.query(Job).count() == 0
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == "<outage-1@example.com>").first() is None
    conn.store.assert_not_called()


def test_classify_document_outage_rolls_back_the_tentative_job_and_unclaims(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op2@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<outage-2@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               return_value={"keys": [group.id], "reason": "matched", "evidence": "Test Consignee Co"}), \
         patch("app.core.classifier.assign_documents_detailed",
               side_effect=AIServiceUnavailable("rate limited")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="outage-op2@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert result["processed"][0]["deferred"] is True
    # The job that was flushed mid-way through (before the AI call failed) must not survive -
    # a half-built job with no documents routed is worse than no job at all.
    assert db_session.query(Job).count() == 0
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == "<outage-2@example.com>").first() is None


def test_a_deferred_message_is_retried_by_the_very_next_ordinary_poll(db_session):
    # The whole point: once the service recovers, the SAME message succeeds on a completely
    # normal poll - no reexamine, no manual intervention, because nothing was ever recorded.
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op3@example.com")
    group, doc = _setup(db_session, tenant)
    # A second template so identify_customer is actually consulted rather than bypassed by
    # the single-connected-template shortcut (see the first test above for the same note).
    db_session.add(TemplateGroup(tenant_id=tenant.id, name="Air Import 3", status="ready"))
    db_session.commit()

    raw = _fake_message("<outage-3@example.com>")

    conn1 = _mock_conn_with_one_message(raw)
    with patch("app.core.email_puller._connect", return_value=conn1), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               side_effect=AIServiceUnavailable("insufficient_quota")):
        first = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                  mail_email="outage-op3@zoho.com", mail_app_password="x",
                                  mail_host="imap.zoho.com")
    assert first["processed"][0]["deferred"] is True
    assert db_session.query(Job).count() == 0

    # Service "recovers" - a completely ordinary poll, reexamine=False, same message still
    # sitting UNSEEN in the mailbox (nothing ever marked it read).
    conn2 = _mock_conn_with_one_message(raw)
    with patch("app.core.email_puller._connect", return_value=conn2), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.identify_customer",
               return_value={"keys": [group.id], "reason": "matched", "evidence": "Test Consignee Co"}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        second = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="outage-op3@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert second["ok"] is True
    assert not second["processed"][0].get("deferred")
    assert db_session.query(Job).count() == 1
