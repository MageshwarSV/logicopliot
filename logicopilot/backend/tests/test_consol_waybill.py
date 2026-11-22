"""Consolidation rule: a zip with a main folder and sub-folders (air consol - master waybill in
the main folder, one sub-folder per house shipment). The Air Waybill comes ONLY from the main
folder; house waybills in sub-folders stay in Unclassified under "Air Waybill". Offline: the
classifier is a stand-in reproducing what happened on SIT (19 Nov 2026, JOB-1CE234): the master
waybill lost the cross-file tie-break and ended up with no slot."""
import io
import zipfile
from email.message import EmailMessage
from unittest.mock import patch

import fitz

from app.core.email_puller import _mailbox_setup, _process_one_message
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _setup(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready", mode="Air Import")
    db_session.add(group)
    db_session.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="BL", order_index=0)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", order_index=1)
    db_session.add_all([awb, inv])
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    assert early is None
    return awb, inv, ctx


def _mail(entries: dict[str, bytes], mid: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    msg = EmailMessage()
    msg["Message-ID"] = mid
    msg["From"] = "ops@example.com"
    msg["Subject"] = "FLEX BILL"
    msg.set_content("attached")
    msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip", filename="consol.zip")
    return msg.as_bytes()


CONSOL = {
    "312-93004472/MASTER HAWB SINA2626873.PDF": None,
    "312-93004472/SINA2626873A - WT/HAWB SINA2626873A.PDF": None,
    "312-93004472/SINA2626873A - WT/INV A.pdf": None,
    "312-93004472/SINA2626873B - INFINEON/HAWB SINA2626873B.PDF": None,
    "312-93004472/SINA2626873B - INFINEON/INV B.pdf": None,
}


def _run(db_session, ctx, raw, awb, inv):
    calls: list[list[str]] = []

    def _classify(prepared, cand_slots, **_k):
        names = [p["name"] for p in prepared]
        calls.append(names)
        out = []
        for n in names:
            if n.startswith("MASTER"):
                # alone it is a waybill; among the house waybills it loses the tie-break
                out.append([{"key": awb.id, "pages": [1], "evidence": "master"}] if len(names) == 1 else [])
            elif n.startswith("HAWB"):
                out.append([{"key": awb.id, "pages": [1], "evidence": "house"}])
            else:
                out.append([{"key": inv.id, "pages": [1], "evidence": "invoice"}])
        return out

    with patch("app.core.docai.ocr_page_image", return_value={"text": "text", "tokens": []}), \
         patch("app.core.classifier_air_import.assign_documents_detailed", side_effect=_classify), \
         patch("app.api.v1.jobs.run_extraction"):
        result = _process_one_message(
            db_session, b"1", raw, ctx, mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False)
    return result, calls


def _placed(db_session, job_id, tdoc_id):
    return sorted(d.original_name for d in db_session.query(JobDocument).filter(
        JobDocument.job_id == job_id, JobDocument.template_document_id == tdoc_id, JobDocument.file_path.isnot(None)))


def test_only_the_main_folders_waybill_is_used_and_house_waybills_wait_in_unclassified(db_session):
    awb, inv, ctx = _setup(db_session)
    entries = {n: _pdf(n.rsplit("/", 1)[-1]) for n in CONSOL}
    result, calls = _run(db_session, ctx, _mail(entries, "<consol-1@example.com>"), awb, inv)
    job_id = result["job_id"]
    assert _placed(db_session, job_id, awb.id) == ["MASTER HAWB SINA2626873.PDF"]
    assert _placed(db_session, job_id, inv.id) == ["INV A.pdf", "INV B.pdf"]
    held = db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job_id).all()
    assert sorted((p.original_filename, p.group_label) for p in held) == [
        ("HAWB SINA2626873A.PDF", "Air Waybill"), ("HAWB SINA2626873B.PDF", "Air Waybill")]
    assert calls[1] == ["MASTER HAWB SINA2626873.PDF"]              # one extra call, the master only


def test_no_waybill_in_the_main_folder_changes_nothing(db_session):
    awb, inv, ctx = _setup(db_session)
    entries = {n: _pdf("x") for n in CONSOL if not n.split("/")[-1].startswith("MASTER")}
    entries["312-93004472/notes.pdf"] = _pdf("notes")                 # a main-folder file, not a waybill
    result, calls = _run(db_session, ctx, _mail(entries, "<consol-2@example.com>"), awb, inv)
    job_id = result["job_id"]
    assert _placed(db_session, job_id, awb.id) == ["HAWB SINA2626873A.PDF", "HAWB SINA2626873B.PDF"]
    assert not db_session.query(UnmatchedUploadPage).filter(
        UnmatchedUploadPage.job_id == job_id, UnmatchedUploadPage.group_label.isnot(None)).count()


def test_a_flat_zip_is_not_a_consolidation(db_session):
    awb, inv, ctx = _setup(db_session)
    entries = {"HAWB X.PDF": _pdf("a"), "HAWB Y.PDF": _pdf("b"), "INV.pdf": _pdf("c")}
    result, calls = _run(db_session, ctx, _mail(entries, "<consol-3@example.com>"), awb, inv)
    assert _placed(db_session, result["job_id"], awb.id) == ["HAWB X.PDF", "HAWB Y.PDF"]
    assert len(calls) == 1                                          # no extra classification
