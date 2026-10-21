"""A single-template mailbox already knows its tenant and template before a single byte of
OCR runs - so its job (and empty document slots) is now created immediately, instead of only
once OCR and classification finish minutes later. While that runs, a page-by-page progress
bar (app/core/email_progress.py) and a new "Document Classifying" stage give the operator
something to actually watch instead of the job appearing to sit frozen.

A shared mailbox (several candidate customers) cannot do any of this - which customer a
message belongs to is exactly what classification is about to work out - and must behave
exactly as it always has: no job until a customer is actually identified."""

import io
import time
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core import email_progress
from app.core.email_puller import _pull_one_mailbox
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user


def _pdf_bytes(pages: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _fake_message(message_id: str, n_pages: int = 1) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "customer@example.com"
    msg["Subject"] = "Shipment docs"
    msg.set_content("Please find attached.")
    msg.add_attachment(_pdf_bytes(n_pages), maintype="application", subtype="pdf", filename="bl.pdf")
    return msg.as_bytes()


def _mock_conn_with_one_message(raw_bytes: bytes):
    conn = MagicMock()
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1 (BODY.PEEK[] {n}", raw_bytes)])
    return conn


def _setup_single_template(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    db_session.commit()
    return tenant, group, doc


def _setup_shared_mailbox(db_session):
    tenant = make_tenant(db_session)
    group_a = TemplateGroup(tenant_id=tenant.id, name="Sea Import A", status="ready")
    group_b = TemplateGroup(tenant_id=tenant.id, name="Sea Import B", status="ready")
    db_session.add_all([group_a, group_b])
    db_session.flush()
    doc_a = TemplateDocument(tenant_id=tenant.id, group_id=group_a.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc_a)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group_a.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Consignee A"))
    db_session.commit()
    return tenant, group_a, doc_a


def test_job_exists_before_ocr_runs_for_a_single_template_mailbox(db_session):
    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op1@example.com")
    raw = _fake_message("<imm-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    seen_job_count_during_first_ocr_call = []

    def _ocr_side_effect(*_a, **_k):
        seen_job_count_during_first_ocr_call.append(db_session.query(Job).count())
        return {"text": "some text", "tokens": []}

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", side_effect=_ocr_side_effect), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   mail_email="owner@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    # The job already existed every time OCR was asked to read a page - including the very
    # first one, well before classification or extraction ever got a chance to run.
    assert seen_job_count_during_first_ocr_call
    assert all(c == 1 for c in seen_job_count_during_first_ocr_call)


def test_single_template_jobs_empty_slots_exist_from_the_start(db_session):
    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op2@example.com")
    raw = _fake_message("<imm-2@example.com>")
    conn = _mock_conn_with_one_message(raw)

    captured = {}

    def _ocr_side_effect(*_a, **_k):
        if not captured:
            job = db_session.query(Job).first()
            captured["slots"] = (
                db_session.query(JobDocument).filter(JobDocument.job_id == job.id).count()
            )
        return {"text": "some text", "tokens": []}

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", side_effect=_ocr_side_effect), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                         mail_email="owner@zoho.com", mail_app_password="x", mail_host="imap.zoho.com")

    # One empty slot per declared template document, present before OCR even started.
    assert captured["slots"] == 1


def test_progress_is_published_during_ocr_and_cleared_before_classification(db_session):
    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op3@example.com")
    raw = _fake_message("<imm-3@example.com>", n_pages=3)
    conn = _mock_conn_with_one_message(raw)

    progress_frames = []

    def _ocr_side_effect(*_a, **_k):
        job = db_session.query(Job).first()
        progress_frames.append(email_progress.latest(job.id))
        return {"text": "some text", "tokens": []}

    progress_during_classify = {}

    def _classify_side_effect(*_a, **_k):
        job = db_session.query(Job).first()
        progress_during_classify["frame"] = email_progress.latest(job.id)
        return [[{"key": doc.id, "pages": [1, 2, 3], "evidence": "bill of lading"}]]

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", side_effect=_ocr_side_effect), \
         patch("app.core.classifier.assign_documents_detailed", side_effect=_classify_side_effect):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   mail_email="owner@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    # ocr_page_image is also called later, during extraction (reading the same page again
    # for field values) - by then progress has already been cleared, so those later calls
    # see None. Only the first three calls - the real document-capture OCR pass - matter here.
    # The mock observes progress from INSIDE the OCR call, before that page's own done-count
    # update runs (which only happens once ocr_page_image has actually returned) - so it sees
    # pages completed so far, not including the one in flight: 0, then 1, then 2.
    capture_phase_frames = progress_frames[:3]
    assert [f["done"] for f in capture_phase_frames] == [0, 1, 2]
    assert all(f["total"] == 3 for f in capture_phase_frames)
    # Cleared the moment classification (the slow per-attachment AI call) actually starts -
    # nothing left over from OCR to show alongside the new "Document Classifying" stage.
    assert progress_during_classify["frame"] is None

    job = db_session.query(Job).first()
    db_session.refresh(job)
    assert email_progress.latest(job.id) is None


def test_document_classifying_stage_is_shown_while_assign_documents_runs(db_session):
    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op4@example.com")
    raw = _fake_message("<imm-4@example.com>")
    conn = _mock_conn_with_one_message(raw)

    stage_seen = {}

    def _classify_side_effect(*_a, **_k):
        job = db_session.query(Job).first()
        db_session.refresh(job)
        stage_seen["stage_override"] = job.stage_override
        return [[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.core.classifier.assign_documents_detailed", side_effect=_classify_side_effect):
        _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                         mail_email="owner@zoho.com", mail_app_password="x", mail_host="imap.zoho.com")

    assert stage_seen["stage_override"] == "Document Classifying"

    # Cleared again once classification is done - must not stay stuck there forever.
    job = db_session.query(Job).first()
    db_session.refresh(job)
    assert job.stage_override is None


def test_ocr_totally_failing_discards_the_early_job(db_session):
    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op5@example.com")
    raw = _fake_message("<imm-5@example.com>")
    conn = _mock_conn_with_one_message(raw)

    # fitz itself fails to open the attachment (corrupt/unsupported) - "not prepared" path.
    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("fitz.open", side_effect=RuntimeError("cannot open")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   mail_email="owner@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    assert result["processed"][0]["reason"] == "attachments could not be read"
    # The job that briefly existed (to show the user something while OCR tried) is gone -
    # not a permanent empty shell nobody can do anything with.
    assert db_session.query(Job).count() == 0


def test_shared_mailbox_still_creates_no_job_until_a_customer_is_identified(db_session):
    # Regression: this feature is scoped to single-template mailboxes only (confirmed with
    # the user) - a shared mailbox must behave exactly as before.
    tenant, group, doc = _setup_shared_mailbox(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op6@example.com")
    raw = _fake_message("<imm-6@example.com>")
    conn = _mock_conn_with_one_message(raw)

    job_count_before_identify = []

    def _identify_side_effect(*_a, **_k):
        job_count_before_identify.append(db_session.query(Job).count())
        return {"keys": [group.id], "reason": "matched", "evidence": "Consignee A"}

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Consignee A", "tokens": []}), \
         patch("app.core.classifier.identify_customer", side_effect=_identify_side_effect), \
         patch("app.core.classifier.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                   mail_email="owner@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True, result
    assert job_count_before_identify == [0]
    assert db_session.query(Job).count() == 1


# --------------------------------------------------------------- GET /jobs/{id}/pull-progress


def test_pull_progress_endpoint_reports_an_active_frame(client, db_session):
    from tests.conftest import login

    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op7@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PROG1", status="draft")
    db_session.add(job)
    db_session.commit()
    email_progress.publish(job.id, 2, 5, time.time() - 10)

    login(client, owner.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/pull-progress")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active"] is True
    assert body["done"] == 2
    assert body["total"] == 5
    assert body["eta_seconds"] is not None


def test_pull_progress_endpoint_reports_inactive_when_nothing_published(client, db_session):
    from tests.conftest import login

    tenant, group, doc = _setup_single_template(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="imm-op8@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PROG2", status="extracted")
    db_session.add(job)
    db_session.commit()

    login(client, owner.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/pull-progress")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"active": False}
