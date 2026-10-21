"""Found live in production: every message routed to a single-template mailbox crashed with
sqlalchemy.orm.exc.DetachedInstanceError the moment the concurrency redesign (_pull_inbox's
harvest-then-shared-pool split) started handing `ctx` - built in one session, during
_mailbox_setup - to a worker running on a DIFFERENT, freshly-opened session (see
_process_with_own_session). Scalar columns read off the detached TemplateGroup (group.id,
.name, .tenant_id, .mode) stayed readable, but `group.documents`, a lazy relationship, tried
to run a fresh query through the now-closed session and blew up - the claim was never
released (no _record/_unclaim ever ran), so the same email just sat there forever, never
becoming a job, with nothing visible to the user explaining why.

These tests reproduce that exact cross-session scenario directly against _process_one_message,
the function that actually crashed - not through two real SessionLocal() instances (overkill
for a unit test), but by expunging the TemplateGroup from the session it was loaded in, which
SQLAlchemy treats identically: any not-yet-loaded relationship access on it raises the same
DetachedInstanceError a truly separate, closed session would."""

import email
import io
from email.message import EmailMessage
from unittest.mock import patch

import fitz

from app.core.email_puller import _mailbox_setup, _process_one_message
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _fake_message(message_id: str, sender: str = "customer@example.com") -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = sender
    msg["Subject"] = "Shipment docs"
    msg.set_content("Please find attached.")
    msg.add_attachment(_pdf_bytes(), maintype="application", subtype="pdf", filename="bl.pdf")
    return msg.as_bytes()


def _make_single_template_ctx(db_session, forced: bool = True):
    """Mirrors exactly what _pull_inbox's harvest phase does: build ctx via _mailbox_setup,
    then detach its TemplateGroup - standing in for that session having already been closed
    (_setup_and_harvest's own `finally: worker_db.close()`) by the time a worker in the
    shared pool actually processes a message against it."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()

    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id if forced else None)
    assert early is None, early
    # The crash's real trigger: the TemplateGroup object ctx carries forward is no longer
    # attached to any session by the time _process_one_message actually runs it.
    live_group = ctx["single_group"] if forced else ctx["by_id"][group.id]
    db_session.expunge(live_group)
    return tenant, group, doc, ctx


def test_process_one_message_does_not_crash_on_a_detached_template_group(db_session):
    tenant, group, doc, ctx = _make_single_template_ctx(db_session)
    raw = _fake_message("<detach-test-1@example.com>")

    with patch("app.core.docai.ocr_page_image",
              return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _process_one_message(
            db_session, b"1", raw, ctx,
            mail_email="owner@zoho.com", mail_app_password="whatever", mail_host="imap.zoho.com",
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False,
        )

    assert result is not None and result.get("job_id"), result
    jobs = db_session.query(Job).filter(Job.group_id == group.id).all()
    assert len(jobs) == 1
    # The real point of the fix: the slot actually got created from the template's documents,
    # not silently skipped because the lazy relationship access was avoided by doing nothing.
    jds = db_session.query(JobDocument).filter(JobDocument.job_id == jobs[0].id).all()
    assert len(jds) == 1
    assert jds[0].template_document_id == doc.id


def test_process_one_message_preserves_document_order_for_a_detached_group(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import Multi", status="ready")
    db_session.add(group)
    db_session.flush()
    # Deliberately inserted out of order - order_index, not insertion order, must win.
    doc_b = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", order_index=1)
    doc_a = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add_all([doc_b, doc_a])
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()

    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    assert early is None
    db_session.expunge(ctx["single_group"])

    raw = _fake_message("<detach-test-2@example.com>")
    with patch("app.core.docai.ocr_page_image",
              return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc_a.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _process_one_message(
            db_session, b"1", raw, ctx,
            mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False,
        )

    assert result is not None and result.get("job_id")
    job = db_session.get(Job, result["job_id"])
    jds = (
        db_session.query(JobDocument).filter(JobDocument.job_id == job.id)
        .join(TemplateDocument, TemplateDocument.id == JobDocument.template_document_id)
        .order_by(TemplateDocument.order_index)
        .all()
    )
    assert [jd.template_document_id for jd in jds] == [doc_a.id, doc_b.id]
