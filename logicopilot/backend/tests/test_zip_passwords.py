"""Password-protected zips (Flex): a saved password opens the zip and the mail is read like any
other; with no working password the job is HELD - "Waiting for Password", nothing read or billed
- and read once, into the same job, when the password is given. Test passwords only; the AI and
OCR are stand-ins."""
import io
import os
import zipfile
from email.message import EmailMessage
from unittest.mock import patch

import fitz
import pytest
from fastapi import HTTPException

from app.api.v1 import jobs as jobs_api
from app.api.v1 import zip_passwords as zip_api
from app.api.v1.jobs import _is_missing_documents
from app.core.deps import TenantScope
from app.core.email_puller import _mailbox_setup, _process_one_message
from app.core.zip_passwords import WAITING_FOR_PASSWORD, candidate_passwords, save_password
from app.models.archive_password import ArchivePassword
from app.models.billable_page_event import BillablePageEvent
from app.models.custom_field import CustomField
from app.models.email_seen import EmailSeen
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user

LOCKED = open(os.path.join(os.path.dirname(__file__), "fixtures", "locked_zipcrypto_test-pass-2.zip"), "rb").read()


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _setup(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready", mode="Air Import")
    db_session.add(group)
    db_session.flush()
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice",
                           order_index=0, is_required=True)
    db_session.add(inv)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    assert early is None
    return tenant, group, inv, ctx


def _mail(mid: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SINA2626873E - MURATA/SINA2626873E.pdf", _pdf("hawb"))
        z.writestr("SINA2626873D - TAIYO/FLXINAUTO.zip", LOCKED)
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["From"] = "ops@flex.example"
    msg["Subject"] = "312-93004472 - SINA2626873 (DTA)"
    msg.set_content("Please find attached.")
    msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip", filename="312-93004472.zip")
    return msg.as_bytes()


def _run(db_session, ctx, raw, inv_id, **kw):
    seen: list[str] = []

    def _assign(prepared, cand_slots, **_k):
        seen.extend(p["name"] for p in prepared)
        return [[{"key": inv_id, "pages": [1], "evidence": "invoice"}] for _ in prepared]

    with patch("app.core.docai.ocr_page_image", return_value={"text": "invoice text", "tokens": []}), \
         patch("app.core.classifier_air_import.assign_documents_detailed", side_effect=_assign), \
         patch("app.api.v1.jobs.run_extraction"):
        result = _process_one_message(
            db_session, b"1", raw, ctx, mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False, **kw)
    return result, seen


def test_a_saved_password_opens_the_zip_and_the_mail_is_read_like_any_other(db_session):
    tenant, group, inv, ctx = _setup(db_session)
    save_password(db_session, tenant_id=tenant.id, label="Flex", password="test-pass-1")
    save_password(db_session, tenant_id=tenant.id, label="Flex", password="test-pass-2")
    db_session.commit()
    result, seen = _run(db_session, ctx, _mail("<flex-1@example.com>"), inv.id)
    assert result.get("job_id") and not result.get("held"), result
    assert sorted(seen) == ["MAL_IV.pdf", "MAL_PL.pdf", "SINA2626873E.pdf"]
    job = db_session.get(Job, result["job_id"])
    assert job.stage_override is None
    assert job.attachment_notes["unlocked"] == ["312-93004472.zip/SINA2626873D - TAIYO/FLXINAUTO.zip"]
    used = db_session.query(ArchivePassword).filter(ArchivePassword.use_count > 0).all()
    assert [r.label for r in used] == ["Flex"] and len(used) == 1   # only the one that worked


def test_no_working_password_holds_the_job_and_reads_nothing(db_session):
    tenant, group, inv, ctx = _setup(db_session)
    result, seen = _run(db_session, ctx, _mail("<flex-2@example.com>"), inv.id)
    assert result.get("held") and result.get("job_id"), result
    assert seen == []                                               # nothing classified
    job = db_session.get(Job, result["job_id"])
    assert job.stage_override == WAITING_FOR_PASSWORD
    assert job.attachment_notes["held"] is True
    assert job.attachment_notes["locked"][0]["files"] == ["MAL_IV.pdf", "MAL_PL.pdf"]
    assert db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).count() == 0
    assert _is_missing_documents(db_session, job)                   # listed under Missing Documents
    seen_row = db_session.query(EmailSeen).filter(EmailSeen.job_id == job.id).one()
    assert seen_row.verdict == "held_password"


def test_the_held_job_is_read_once_into_the_same_job_when_the_password_is_given(db_session):
    tenant, group, inv, ctx = _setup(db_session)
    raw = _mail("<flex-3@example.com>")
    held, _ = _run(db_session, ctx, raw, inv.id)
    job_id = held["job_id"]
    result, seen = _run(db_session, ctx, raw, inv.id, resume_job_id=job_id, extra_passwords=["test-pass-2"])
    assert result.get("job_id") == job_id, result                   # the same job, not a new one
    assert sorted(seen) == ["MAL_IV.pdf", "MAL_PL.pdf", "SINA2626873E.pdf"]
    job = db_session.get(Job, job_id)
    assert job.stage_override is None
    assert db_session.query(Job).filter(Job.group_id == group.id).count() == 1
    assert db_session.query(JobDocument).filter(JobDocument.job_id == job_id, JobDocument.file_path.isnot(None)).count() == 3


def test_process_without_locked_files_reads_the_rest(db_session):
    tenant, group, inv, ctx = _setup(db_session)
    raw = _mail("<flex-4@example.com>")
    held, _ = _run(db_session, ctx, raw, inv.id)
    result, seen = _run(db_session, ctx, raw, inv.id, resume_job_id=held["job_id"], skip_locked=True)
    assert seen == ["SINA2626873E.pdf"]
    assert db_session.get(Job, held["job_id"]).attachment_notes["locked"][0]["files"] == ["MAL_IV.pdf", "MAL_PL.pdf"]


def test_unlock_checks_the_password_first_and_locks_after_five_wrong_tries(db_session, monkeypatch, tmp_path):
    tenant, group, inv, ctx = _setup(db_session)
    monkeypatch.setattr("app.core.job_email.job_email_dir", lambda jid: tmp_path / jid)
    held, _ = _run(db_session, ctx, _mail("<flex-5@example.com>"), inv.id)
    job_id = held["job_id"]
    (tmp_path / job_id).mkdir(parents=True, exist_ok=True)
    (tmp_path / job_id / "original.eml").write_bytes(_mail("<flex-5@example.com>"))
    user = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-zip@example.com")
    scope = TenantScope(tenant_id=tenant.id, is_super_admin=False)
    started: list = []
    monkeypatch.setattr("threading.Thread.start", lambda self: started.append(self.name))

    for i in range(4):
        with pytest.raises(HTTPException) as exc:
            jobs_api.unlock_held_job(job_id, jobs_api.UnlockHeldJobPayload(password="wrong"),
                                     db=db_session, scope=scope, user=user)
        assert exc.value.status_code == 422 and "Incorrect password" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        jobs_api.unlock_held_job(job_id, jobs_api.UnlockHeldJobPayload(password="wrong"),
                                 db=db_session, scope=scope, user=user)
    assert "Locked for 15 minutes" in exc.value.detail
    with pytest.raises(HTTPException) as exc:                      # even the right one, now
        jobs_api.unlock_held_job(job_id, jobs_api.UnlockHeldJobPayload(password="test-pass-2"),
                                 db=db_session, scope=scope, user=user)
    assert exc.value.status_code == 429
    assert started == []

    job = db_session.get(Job, job_id)
    job.attachment_notes = {k: v for k, v in job.attachment_notes.items() if k != "locked_until"}
    db_session.commit()
    jobs_api.unlock_held_job(job_id, jobs_api.UnlockHeldJobPayload(password="test-pass-2", remember=True, label="Flex"),
                             db=db_session, scope=scope, user=user)
    assert len(started) == 1                                        # reading started in the background
    assert [pw for _id, pw in candidate_passwords(db_session, tenant.id)] == ["test-pass-2"]


def test_the_zip_password_api_never_returns_a_password(db_session):
    tenant = make_tenant(db_session)
    user = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-zip2@example.com")
    zip_api.add_zip_password(zip_api.ZipPasswordIn(label="Flex", password="test-pass-1",
                                                   sender_domains=["@Flex.example"]),
                             tenant_id=None, db=db_session, user=user)
    zip_api.add_zip_password(zip_api.ZipPasswordIn(label="Flex", password="test-pass-1"),   # same again
                             tenant_id=None, db=db_session, user=user)
    rows = zip_api.list_zip_passwords(tenant_id=None, db=db_session, user=user)
    assert len(rows) == 1 and rows[0].sender_domains == ["flex.example"]
    assert "test-pass-1" not in rows[0].model_dump_json()
    stored = db_session.query(ArchivePassword).one()
    assert "test-pass-1" not in stored.password_encrypted          # encrypted at rest


def test_auto_extract_off_places_documents_but_never_starts_extraction(db_session):
    tenant, group, inv, ctx = _setup(db_session)
    group.auto_extract = False
    db_session.commit()
    save_password(db_session, tenant_id=tenant.id, label="Flex", password="test-pass-2")
    db_session.commit()
    with patch("app.core.docai.ocr_page_image", return_value={"text": "invoice", "tokens": []}), \
         patch("app.core.classifier_air_import.assign_documents_detailed",
               side_effect=lambda prepared, c, **k: [[{"key": inv.id, "pages": [1], "evidence": "e"}] for _ in prepared]), \
         patch("app.api.v1.jobs.run_extraction", side_effect=AssertionError("extraction started")):
        result = _process_one_message(
            db_session, b"1", _mail("<flex-6@example.com>"), ctx, mail_email=None, mail_app_password=None,
            mail_host=None, extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False)
    job = db_session.get(Job, result["job_id"])
    assert job.status == "draft"                                    # placed, waiting for Extract
    assert db_session.query(JobDocument).filter(JobDocument.job_id == job.id, JobDocument.file_path.isnot(None)).count() == 3
