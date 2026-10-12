"""email_puller.py now stages every kept page as an UnmatchedUploadPage BEFORE classification
runs, exactly like Smart Upload already does, and prunes only the pages a real claim actually
covers - a real document page that is skipped or loses a same-slot arbitration to another
page must surface to an operator as Unclassified, never vanish with no trace. Confirmed live:
a genuine "PACKING LIST" page sitting in a real mail attachment was silently dropped (no job
document, no Unclassified row either) while a decorative email-signature icon won that same
slot instead."""
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.email_puller import _pull_one_mailbox
from app.models.email_seen import EmailSeen
from app.models.job import JobDocument, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user


def _two_page_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
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
    msg.add_attachment(_two_page_pdf_bytes(), maintype="application", subtype="pdf",
                       filename="combined.pdf")
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
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", order_index=0)
    db_session.add(doc)
    db_session.commit()
    return group, doc


def test_an_unclaimed_page_becomes_an_unmatched_upload_page_row(db_session):
    """Page 1 is claimed by the Invoice slot; page 2 is not claimed by anything - it must
    survive as a real, inspectable Unclassified row, not disappear."""
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="pages-op@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<pages-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some real page text", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "invoice no 123"}]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="pages-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    seen = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<pages-1@example.com>").first()
    assert seen is not None
    assert seen.verdict == "matched"
    job_id = seen.job_id
    assert job_id is not None

    unmatched = db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job_id).all()
    assert len(unmatched) == 1
    assert unmatched[0].page_number == 2
    assert unmatched[0].original_filename == "combined.pdf"

    filled = db_session.query(JobDocument).filter(
        JobDocument.job_id == job_id, JobDocument.template_document_id == doc.id).first()
    assert filled is not None and filled.file_path is not None


def test_nothing_claimed_but_real_text_keeps_the_job_with_both_pages_unclassified(db_session):
    """Neither page matches any slot (e.g. the template is missing a document type), but both
    pages have real, readable content - the job must be KEPT (not deleted) so an operator can
    assign them by hand, and the verdict stays "matched", not "unrouted"."""
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="nocla-op@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<pages-2@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some real page text", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed", return_value=[[]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="nocla-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    seen = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<pages-2@example.com>").first()
    assert seen is not None
    assert seen.verdict == "matched"
    assert seen.job_id is not None

    unmatched = db_session.query(UnmatchedUploadPage).filter(
        UnmatchedUploadPage.job_id == seen.job_id).all()
    assert len(unmatched) == 2
    assert sorted(u.page_number for u in unmatched) == [1, 2]


def test_a_weak_ai_second_opinion_claim_never_fills_a_slot_and_stays_unclassified(db_session):
    """The exact real production bug: a plain single-page House BL also got an "ai_second"
    claim onto the Packing List slot (a weak, loosely-worded second-opinion call, not a
    confident primary classification or a deterministic regex hit). That weak claim must be
    dropped rather than silently duplicating the BL into Packing List too - the page it names
    stays Unclassified since nothing STRONG claimed it there."""
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="weak-op@example.com")
    group, doc = _setup(db_session, tenant)
    pl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                             doc_type="PackingList", order_index=1)
    db_session.add(pl_doc)
    db_session.commit()

    raw = _fake_message("<weak-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    # Page 1: a confident primary ("model") claim on Invoice. Page 2: nothing strong claims
    # it, but a weak "ai_second" claim names it for Packing List anyway.
    claims = [[
        {"key": doc.id, "pages": [1], "evidence": "invoice no 123", "source": "model"},
        {"key": pl_doc.id, "pages": [2], "evidence": "AI-verified combined document: weak guess",
         "source": "ai_second"},
    ]]
    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some real page text", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed", return_value=claims):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="weak-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    seen = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<weak-1@example.com>").first()
    assert seen is not None and seen.job_id is not None

    # Packing List slot must stay empty - the weak claim never filled it.
    pl_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == seen.job_id, JobDocument.template_document_id == pl_doc.id).first()
    assert pl_jd is not None and pl_jd.file_path is None

    # Invoice slot DID get filled, by the strong claim.
    inv_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == seen.job_id, JobDocument.template_document_id == doc.id).first()
    assert inv_jd is not None and inv_jd.file_path is not None

    # Page 2 (named only by the weak claim) stays Unclassified; page 1 (claimed strongly) does not.
    unmatched = db_session.query(UnmatchedUploadPage).filter(
        UnmatchedUploadPage.job_id == seen.job_id).all()
    assert [u.page_number for u in unmatched] == [2]


def test_nothing_claimed_and_no_real_text_still_deletes_the_job_as_unrouted(db_session, monkeypatch):
    """Every page is genuinely blank/unreadable (OCR found nothing) - there is truly nothing
    for an operator to resolve, so the job must still be deleted and the message recorded
    "unrouted", exactly as before this change existed."""
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="blank-op@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<pages-3@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed", return_value=[[]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, operator_id=owner.id,
                                   mail_email="blank-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    seen = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<pages-3@example.com>").first()
    assert seen is not None
    assert seen.verdict == "unrouted"
    assert seen.job_id is None
    # Nothing should be left behind for a job that no longer exists.
    assert db_session.query(UnmatchedUploadPage).count() == 0
