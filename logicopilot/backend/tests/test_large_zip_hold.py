"""Hold extraction for a large zip (TemplateGroup.large_zip_hold / large_zip_mb): a mail with a
zip over the limit is read, classified and placed, then waits for Run extraction; every other
mail extracts automatically, as before. Offline: AI and OCR are stand-ins."""
import io
import os
import zipfile
from email.message import EmailMessage
from unittest.mock import patch

import fitz

from app.api.v1.jobs import _maybe_auto_extract
from app.core.email_puller import _mailbox_setup, _process_one_message
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _setup(db_session, hold: bool):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready", mode="Air Import",
                          large_zip_hold=hold, large_zip_mb=1)
    db_session.add(group)
    db_session.flush()
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", is_required=True)
    db_session.add(inv)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    return inv, ctx


def _mail(mid: str, padding_mb: float) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("INV.pdf", _pdf("invoice"))
        if padding_mb:
            z.writestr("scan.bin", os.urandom(int(padding_mb * 1024 * 1024)))   # not compressible
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["From"] = "ops@example.com"
    msg["Subject"] = "docs"
    msg.set_content("attached")
    msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip", filename="docs.zip")
    return msg.as_bytes()


def _run(db_session, ctx, raw, inv):
    with patch("app.core.docai.ocr_page_image", return_value={"text": "invoice", "tokens": []}), \
         patch("app.core.classifier_air_import.assign_documents_detailed",
               side_effect=lambda prepared, c, **k: [[{"key": inv.id, "pages": [1], "evidence": "e"}] for _ in prepared]), \
         patch("app.api.v1.jobs.run_extraction") as extract:
        result = _process_one_message(
            db_session, b"1", raw, ctx, mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False)
    return result, extract.called


def test_a_zip_over_the_limit_is_placed_but_not_extracted(db_session):
    inv, ctx = _setup(db_session, hold=True)
    result, extracted = _run(db_session, ctx, _mail("<big@example.com>", 1.5), inv)
    job = db_session.get(Job, result["job_id"])
    assert not extracted
    assert job.attachment_notes["large_zip"]["limit_mb"] == 1 and job.attachment_notes["large_zip"]["size_mb"] > 1
    assert db_session.query(JobDocument).filter(JobDocument.job_id == job.id, JobDocument.file_path.isnot(None)).count() == 1
    _maybe_auto_extract(db_session, job)                            # a later upload: still waits
    assert job.status == "draft"


def test_a_small_zip_extracts_automatically_as_before(db_session):
    inv, ctx = _setup(db_session, hold=True)
    result, extracted = _run(db_session, ctx, _mail("<small@example.com>", 0), inv)
    assert extracted
    assert "large_zip" not in (db_session.get(Job, result["job_id"]).attachment_notes or {})


def test_rule_off_means_even_a_big_zip_extracts(db_session):
    inv, ctx = _setup(db_session, hold=False)
    result, extracted = _run(db_session, ctx, _mail("<big-off@example.com>", 1.5), inv)
    assert extracted
