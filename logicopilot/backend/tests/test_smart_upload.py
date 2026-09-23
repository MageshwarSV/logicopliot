"""POST /jobs/{id}/smart-upload end to end: real multipart upload, real PDF rendering,
real file writes and slot creation - only OCR (ocr_page_image) and classification
(assign_documents_detailed) are mocked, since those need real Document AI/OpenAI
credentials and are already covered on their own (test_classifier.py)."""

from unittest.mock import patch

import fitz
import pytest

from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes(n_pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _setup_job(db_session, *, doc_specs, tenant=None, operator=None, job_status="extracted",
                assigned_operator_id=None):
    """doc_specs: [(name, doc_type), ...]. Returns (job, {doc_name: TemplateDocument})."""
    tenant = tenant or make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Test Group", status="ready")
    db_session.add(group)
    db_session.flush()
    docs = {}
    for i, (name, doc_type) in enumerate(doc_specs):
        d = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name,
                              doc_type=doc_type, order_index=i)
        db_session.add(d)
        docs[name] = d
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SMARTUP",
              status=job_status, assigned_operator_id=assigned_operator_id)
    db_session.add(job)
    db_session.flush()
    for d in docs.values():
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id,
                                    template_document_id=d.id, page_count=0))
    db_session.commit()
    return tenant, job, docs


def _upload(client, job_id, files):
    """files: [(filename, bytes, content_type)]"""
    return client.post(f"/api/v1/jobs/{job_id}/smart-upload",
                        files=[("files", f) for f in files])


def _claim(key, pages, evidence="evidence"):
    return {"key": key, "pages": pages, "evidence": evidence}


def _mock_ocr():
    return patch("app.api.v1.jobs.ocr_page_image", return_value={"text": "some text", "tokens": []})


# --------------------------------------------------------------------------- #
# Positive cases
# --------------------------------------------------------------------------- #

def test_single_file_matches_single_slot(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op1@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"][0]["matched"] == ["Invoice"]

    jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).one()
    assert jd.file_path is not None
    assert jd.page_count == 1
    assert jd.original_name == "inv.pdf"


def test_combined_invoice_cum_packing_list_single_page_fills_both_slots(client, db_session):
    # The real case: a "SHIPPING INVOICE CUM PACKING LIST" - ONE page that is genuinely
    # both an Invoice and a Packing List at once, not two separate pages. Both slots must
    # be filled, both from that same single page.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice"), ("PackingList", "PackingList")])
    op = make_user(db_session, role="operator", tenant=tenant, email="combo-1page@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1]), _claim(docs["PackingList"].id, [1])]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("shipping_invoice_cum_pl.pdf", _pdf_bytes(1), "application/pdf")])

    assert resp.status_code == 200, resp.text
    assert set(resp.json()["results"][0]["matched"]) == {"Invoice", "PackingList"}

    inv_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id).one()
    pl_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["PackingList"].id).one()
    assert inv_jd.file_path is not None and pl_jd.file_path is not None
    assert inv_jd.page_count == 1
    assert pl_jd.page_count == 1


def test_combined_file_splits_across_two_slots(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("BL", "BL"), ("PackingList", "PackingList")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op2@example.com")
    login(client, op.email)

    claims = [[_claim(docs["BL"].id, [1]), _claim(docs["PackingList"].id, [2])]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("combo.pdf", _pdf_bytes(2), "application/pdf")])

    assert resp.status_code == 200, resp.text
    assert set(resp.json()["results"][0]["matched"]) == {"BL", "PackingList"}

    bl_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["BL"].id).one()
    pl_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["PackingList"].id).one()
    assert bl_jd.page_count == 1
    assert pl_jd.page_count == 1
    # Each split-out file is its own physical PDF, not the whole combined original.
    assert bl_jd.file_path != pl_jd.file_path


def test_three_files_all_land_in_the_same_slot(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op3@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("inv1.pdf", _pdf_bytes(1), "application/pdf"),
            ("inv2.pdf", _pdf_bytes(1), "application/pdf"),
            ("inv3.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).order_by(JobDocument.file_index).all()
    assert len(rows) == 3
    assert {r.original_name for r in rows} == {"inv1.pdf", "inv2.pdf", "inv3.pdf"}
    assert len({r.file_path for r in rows}) == 3  # three distinct files on disk


def test_zip_upload_extracts_and_processes_both_pdfs(client, db_session):
    import io
    import zipfile

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice"), ("BL", "BL")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op4@example.com")
    login(client, op.email)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inv.pdf", _pdf_bytes(1))
        z.writestr("bl.pdf", _pdf_bytes(1))
    zip_bytes = buf.getvalue()

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["BL"].id, [1])]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("bundle.zip", zip_bytes, "application/zip")])

    assert resp.status_code == 200, resp.text
    matched = {r["filename"]: r["matched"] for r in resp.json()["results"]}
    assert matched == {"inv.pdf": ["Invoice"], "bl.pdf": ["BL"]}


def test_one_pdf_with_three_invoice_instances_splits_into_three_files(client, db_session):
    # The ORIGINAL multi-instance bug: not three separate uploads, ONE 3-page PDF that IS
    # three physically distinct invoices. Each must become its own file on disk.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="multi-inst@example.com")
    login(client, op.email)

    claims = [[
        _claim(docs["Invoice"].id, [1], "invoice one"),
        _claim(docs["Invoice"].id, [2], "invoice two"),
        _claim(docs["Invoice"].id, [3], "invoice three"),
    ]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("three_invoices.pdf", _pdf_bytes(3), "application/pdf")])

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["matched"] == ["Invoice", "Invoice", "Invoice"]
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).order_by(JobDocument.file_index).all()
    assert len(rows) == 3
    assert all(r.page_count == 1 for r in rows)
    assert len({r.file_path for r in rows}) == 3


def test_mixed_multi_type_and_multi_instance_in_one_file(client, db_session):
    # The hardest real combination: page 1 is a BL, pages 2 and 3 are two SEPARATE invoices,
    # all in one PDF.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("BL", "BL"), ("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="mixed@example.com")
    login(client, op.email)

    claims = [[
        _claim(docs["BL"].id, [1], "bill of lading text"),
        _claim(docs["Invoice"].id, [2], "invoice A"),
        _claim(docs["Invoice"].id, [3], "invoice B"),
    ]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("mixed.pdf", _pdf_bytes(3), "application/pdf")])

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["matched"] == ["BL", "Invoice", "Invoice"]
    bl_rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["BL"].id).all()
    inv_rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id).all()
    assert len(bl_rows) == 1 and bl_rows[0].page_count == 1
    assert len(inv_rows) == 2
    assert all(r.page_count == 1 for r in inv_rows)


def test_uploading_to_a_slot_that_already_has_a_file_adds_a_row_not_overwrites(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="addrow@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        first = _upload(client, job.id, [("first.pdf", _pdf_bytes(1), "application/pdf")])
    assert first.status_code == 200, first.text

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        second = _upload(client, job.id, [("second.pdf", _pdf_bytes(1), "application/pdf")])
    assert second.status_code == 200, second.text

    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).order_by(JobDocument.file_index).all()
    assert len(rows) == 2
    assert [r.original_name for r in rows] == ["first.pdf", "second.pdf"]
    assert rows[0].file_index < rows[1].file_index


def test_mixed_supported_and_unsupported_file_in_one_request(client, db_session):
    # The good file must still process correctly even though a bad one rides along.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="mixedreq@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        resp = _upload(client, job.id, [
            ("readme.txt", b"not a document", "text/plain"),
            ("inv.pdf", _pdf_bytes(1), "application/pdf"),
        ])
    assert resp.status_code == 200, resp.text
    # Only the real PDF produced a result entry - the .txt is silently absent (same
    # confirmed gap as the standalone case).
    assert len(resp.json()["results"]) == 1
    assert resp.json()["results"][0]["filename"] == "inv.pdf"


def test_direct_image_upload_is_accepted(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="imgdirect@example.com")
    login(client, op.email)

    doc = fitz.open()
    page = doc.new_page()
    pix = page.get_pixmap()
    png_bytes = pix.tobytes("png")
    doc.close()

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        resp = _upload(client, job.id, [("scan.png", png_bytes, "image/png")])

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["matched"] == ["Invoice"]


def test_zip_with_only_unsupported_files_is_rejected_as_empty(client, db_session):
    import io
    import zipfile

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="ziptxt@example.com")
    login(client, op.email)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", b"nothing useful")
        z.writestr("notes.docx", b"also nothing useful")
    resp = _upload(client, job.id, [("junk.zip", buf.getvalue(), "application/zip")])

    # Unlike a direct unsupported-extension upload (silently dropped, 200 with empty
    # results), a ZIP whose contents are ALL unsupported never even makes it into `files`
    # at all, so the endpoint correctly reports the whole request as empty.
    assert resp.status_code == 422
    assert "No PDF/image files found" in resp.json()["detail"]


def test_zip_ignores_unsupported_entries_but_still_processes_the_supported_one(client, db_session):
    import io
    import zipfile

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="zipmixed@example.com")
    login(client, op.email)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", b"nothing useful")
        z.writestr("inv.pdf", _pdf_bytes(1))
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        resp = _upload(client, job.id, [("mixed.zip", buf.getvalue(), "application/zip")])

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["results"]) == 1
    assert resp.json()["results"][0]["filename"] == "inv.pdf"


def test_zip_and_direct_file_together_in_one_request(client, db_session):
    import io
    import zipfile

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice"), ("BL", "BL")])
    op = make_user(db_session, role="operator", tenant=tenant, email="zipplusdirect@example.com")
    login(client, op.email)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inv.pdf", _pdf_bytes(1))

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["BL"].id, [1])]]
    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("bundle.zip", buf.getvalue(), "application/zip"),
            ("bl.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    matched = {r["filename"]: r["matched"] for r in resp.json()["results"]}
    assert matched == {"inv.pdf": ["Invoice"], "bl.pdf": ["BL"]}


def test_template_group_with_no_documents_at_all_never_crashes(client, db_session):
    tenant, job, _docs = _setup_job(db_session, doc_specs=[])
    op = make_user(db_session, role="operator", tenant=tenant, email="nodocs@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=[[]]):
        resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["matched"] is None


@pytest.mark.parametrize("role", ["operator", "super_admin", "admin"])
def test_allowed_roles_can_smart_upload(client, db_session, role):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    if role == "operator":
        user = make_user(db_session, role="operator", tenant=tenant, email=f"r-{role}@example.com")
    else:
        user = make_user(db_session, role=role, tenant=None if role != "operator" else tenant,
                         email=f"r-{role}@example.com")
    login(client, user.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[_claim(docs["Invoice"].id, [1])]]):
        resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# Negative / edge cases
# --------------------------------------------------------------------------- #

def test_tenant_admin_is_forbidden(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-su@example.com")
    login(client, ta.email)

    resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 403


def test_no_files_at_all_is_rejected(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op5@example.com")
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/smart-upload", files=[])
    assert resp.status_code == 422


def test_corrupt_zip_is_rejected(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op6@example.com")
    login(client, op.email)

    resp = _upload(client, job.id, [("bad.zip", b"not a real zip file", "application/zip")])
    assert resp.status_code == 422
    assert "invalid ZIP" in resp.json()["detail"]


def test_unreadable_pdf_bytes_reported_as_error_not_a_crash(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op7@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=[]):
        resp = _upload(client, job.id, [("garbage.pdf", b"this is not a pdf at all", "application/pdf")])

    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["filename"] == "garbage.pdf"
    assert result["error"] == "unreadable"
    assert result["matched"] == []


def test_job_not_found_returns_404(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op8@example.com")
    login(client, op.email)

    resp = _upload(client, "does-not-exist", [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 404


def test_operator_cannot_smart_upload_another_operators_job(client, db_session):
    tenant = make_tenant(db_session)
    other_op = make_user(db_session, role="operator", tenant=tenant, email="owner@example.com")
    _, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")], tenant=tenant,
                              assigned_operator_id=other_op.id)
    me = make_user(db_session, role="operator", tenant=tenant, email="not-owner@example.com")
    login(client, me.email)

    resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 404


def test_file_matching_nothing_reports_no_match_without_error(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op9@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=[[]]):
        resp = _upload(client, job.id, [("random.pdf", _pdf_bytes(1), "application/pdf")])

    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["matched"] is None
    # No file was actually written into the slot.
    jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id).one()
    assert jd.file_path is None


def test_txt_file_produces_no_result_entry(client, db_session):
    """Documents ACTUAL current behaviour rather than assuming: a direct (non-zip) upload
    with an unsupported extension is accepted past the initial file-presence check (it is
    appended regardless of extension), then silently skipped in the prepare loop and never
    appears in `results` at all - not the same as an explicit per-file error. Worth knowing
    if a real customer uploads e.g. a .docx by mistake: they get no feedback about it."""
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="op10@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=[]):
        resp = _upload(client, job.id, [("notes.txt", b"just some notes", "text/plain")])

    assert resp.status_code == 200, resp.text
    # Confirmed gap: the unsupported file is accepted but never reported in results.
    assert resp.json()["results"] == []
