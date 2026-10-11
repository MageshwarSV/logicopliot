"""resolve_pending_email was the one entry point still missing the extraction_engine wiring
smart_upload and email_puller already have - its own docstring promised "the SAME classifier
... smart-upload use", but it called assign_documents_detailed with no engine/vision_model/
per_page_vision at all, so it silently used the old default text classifier regardless of the
tenant's own Settings toggle. Found live: a combined attachment (several distinct document
types bundled in one PDF) resolved through this screen stayed on whole-file text
classification and landed entirely under one slot instead of being split page by page."""
from unittest.mock import patch

import fitz

from app.core.system_settings import set_extraction_engine
from app.models.pending_email import PendingEmail
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _setup(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", order_index=0)
    db_session.add(doc)
    db_session.commit()
    return tenant, group, doc


def _make_pending_email_with_attachment(db_session):
    from app.api.v1.pending_mail import _pending_dir

    row = PendingEmail(message_id="msg-1@example.com", sender="a@b.com", subject="docs",
                       reason="no match", status="pending",
                       attachments=[{"name": "invoice.pdf", "ext": ".pdf", "path": "att1.pdf",
                                    "text": "some text", "drop_pages": [], "page_count": 1}])
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    pdir = _pending_dir(row.id)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "att1.pdf").write_bytes(_pdf_bytes())
    return row


def test_default_engine_reaches_assign_documents_detailed_unchanged(client, db_session):
    tenant, group, doc = _setup(db_session)
    row = _make_pending_email_with_attachment(db_session)
    admin = make_user(db_session, role="super_admin", email="pm-op1@example.com")
    login(client, admin.email)

    with patch("app.api.v1.pending_mail.assign_documents_detailed",
              return_value=[[{"key": doc.id, "pages": [1], "evidence": "e"}]]) as mock_assign, \
         patch("app.api.v1.jobs._start_extraction_background"):
        resp = client.post(f"/api/v1/pending-emails/{row.id}/resolve", json={"group_id": group.id})

    assert resp.status_code == 200, resp.text
    assert mock_assign.call_args.kwargs["engine"] == "ocr_gpt4o_mini"
    assert mock_assign.call_args.kwargs["per_page_vision"] is True


def test_vision_engine_setting_reaches_assign_documents_detailed_with_the_configured_model(
    client, db_session,
):
    tenant, group, doc = _setup(db_session)
    set_extraction_engine(db_session, "gpt5_mini_vision")
    row = _make_pending_email_with_attachment(db_session)
    admin = make_user(db_session, role="super_admin", email="pm-op2@example.com")
    login(client, admin.email)

    with patch("app.api.v1.pending_mail.assign_documents_detailed",
              return_value=[[{"key": doc.id, "pages": [1], "evidence": "e"}]]) as mock_assign, \
         patch("app.api.v1.jobs._start_extraction_background"):
        resp = client.post(f"/api/v1/pending-emails/{row.id}/resolve", json={"group_id": group.id})

    assert resp.status_code == 200, resp.text
    assert mock_assign.call_args.kwargs["engine"] == "gpt5_mini_vision"
    assert mock_assign.call_args.kwargs["vision_model"] == "gpt-5-mini"
    assert mock_assign.call_args.kwargs["per_page_vision"] is True
    # Page images were actually rendered from the saved blob for the vision engine - the real
    # fix, not just the kwarg being passed through.
    prepared_arg = mock_assign.call_args.args[0]
    assert len(prepared_arg[0]["page_images"]) == 1
