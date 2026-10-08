"""Email auto-pull now classifies every page of a combined/multi-page attachment from its
own image under the vision engine, the same fix Smart Upload already got - a page landing in
the wrong slot is just as real a bug arriving by mail as uploaded by hand. Confirms
_pull_one_mailbox reaches assign_documents_detailed with per_page_vision=True regardless of
which engine is configured (the flag is a no-op under the default engine, but it must still
be passed, mirroring smart_upload's own call)."""
import io
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.email_puller import _pull_one_mailbox
from app.core.system_settings import set_extraction_engine
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
    db_session.commit()
    return group, doc


def test_email_pull_reaches_assign_documents_detailed_with_per_page_vision(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="vis-op@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<vis-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "bill of lading", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]) as mock_assign:
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="vis-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert mock_assign.call_args.kwargs["engine"] == "ocr_gpt4o_mini"
    assert mock_assign.call_args.kwargs["per_page_vision"] is True


def test_email_pull_under_the_vision_engine_also_passes_per_page_vision(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="vis-op2@example.com")
    group, doc = _setup(db_session, tenant)
    set_extraction_engine(db_session, "gpt5_mini_vision")

    raw = _fake_message("<vis-2@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "bill of lading", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]) as mock_assign:
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="vis-op2@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert mock_assign.call_args.kwargs["engine"] == "gpt5_mini_vision"
    assert mock_assign.call_args.kwargs["vision_model"] == "gpt-5-mini"
    assert mock_assign.call_args.kwargs["per_page_vision"] is True
