"""Invoice/PackingList documents are grouped or split by the invoice number printed on them
(app.core.doc_sets.extract_invoice_number_from_text + same_number), not by which uploaded
file they arrived in or how many files were uploaded - items 2 and 3 of the requested feature
list. OCR and classification are mocked (same reasoning as test_smart_upload.py); `side_effect`
on ocr_page_image supplies realistic per-page text so the real regex/matching logic runs."""

from unittest.mock import patch

from app.models.job import JobDocument
from tests.conftest import login, make_user
from tests.test_smart_upload import _claim, _pdf_bytes, _setup_job, _upload


def _ocr(*texts: str):
    return patch("app.api.v1.jobs.ocr_page_image",
                 side_effect=[{"text": t, "tokens": []} for t in texts])


def test_two_separate_invoice_files_with_same_number_merge_into_one_document(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="merge1@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])]]
    with _ocr("COMMERCIAL INVOICE\nInvoice No: INV-0505\nFirst page",
              "Invoice No: INV-0505\nContinuation / second page"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("inv_part1.pdf", _pdf_bytes(1), "application/pdf"),
            ("inv_part2.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).all()
    assert len(rows) == 1, "same invoice number across two files must merge into one document"
    assert rows[0].page_count == 2

    body = resp.json()
    assert len(body["results"]) == 2
    assert all(r["matched"] == ["Invoice"] for r in body["results"])


def test_two_separate_invoice_files_with_different_numbers_stay_separate(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="nomerge1@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])]]
    with _ocr("Invoice No: INV-0505", "Invoice No: INV-0777"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("inv_a.pdf", _pdf_bytes(1), "application/pdf"),
            ("inv_b.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).all()
    assert len(rows) == 2, "different invoice numbers must stay as separate documents"
    assert {r.page_count for r in rows} == {1}


def test_one_combined_file_with_two_invoice_numbers_splits_by_number(client, db_session):
    # Same slot, same uploaded FILE, two claims - must split into two documents because the
    # pages carry different invoice numbers, not stay combined just because it's one file.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="splitsame@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1]), _claim(docs["Invoice"].id, [2])]]
    with _ocr("Invoice No: INV-0100", "Invoice No: INV-0200"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [("combo.pdf", _pdf_bytes(2), "application/pdf")])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).all()
    assert len(rows) == 2, "two different invoice numbers in one file must split into two documents"
    assert {r.page_count for r in rows} == {1}
    assert len({r.file_path for r in rows}) == 2


def test_packing_lists_are_grouped_by_invoice_number_too(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("PackingList", "PackingList")])
    op = make_user(db_session, role="operator", tenant=tenant, email="plmerge@example.com")
    login(client, op.email)

    claims = [[_claim(docs["PackingList"].id, [1])], [_claim(docs["PackingList"].id, [1])]]
    with _ocr("PACKING LIST\nInvoice No: INV-0505\npage 1 of 2",
              "Invoice No: INV-0505\npage 2 of 2"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("pl_1.pdf", _pdf_bytes(1), "application/pdf"),
            ("pl_2.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["PackingList"].id
    ).all()
    assert len(rows) == 1
    assert rows[0].page_count == 2


def test_invoice_and_packing_list_never_merge_across_slots(client, db_session):
    # Same invoice number on both, but they belong to DIFFERENT slots - grouping is always
    # scoped to one slot at a time, never across an Invoice and its own Packing List.
    tenant, job, docs = _setup_job(
        db_session, doc_specs=[("Invoice", "Invoice"), ("PackingList", "PackingList")])
    op = make_user(db_session, role="operator", tenant=tenant, email="noslotmix@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["PackingList"].id, [1])]]
    with _ocr("Invoice No: INV-0505", "Invoice No: INV-0505"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("inv.pdf", _pdf_bytes(1), "application/pdf"),
            ("pl.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    inv_rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id).all()
    pl_rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["PackingList"].id).all()
    assert len(inv_rows) == 1
    assert len(pl_rows) == 1
    assert inv_rows[0].file_path != pl_rows[0].file_path


def test_abbreviated_invoice_number_still_matches_full_form(client, db_session):
    # A packing list printing the short form ("0505") of what the invoice itself prints in
    # full ("INV-000505") is the same invoice - see doc_sets.same_number's suffix rule.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="abbrev@example.com")
    login(client, op.email)

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])]]
    with _ocr("Invoice No: 0505", "Invoice No: INV-000505"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("short.pdf", _pdf_bytes(1), "application/pdf"),
            ("full.pdf", _pdf_bytes(1), "application/pdf"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).all()
    assert len(rows) == 1
    assert rows[0].page_count == 2


def test_mixed_pdf_and_image_with_same_number_does_not_crash_and_stays_separate(client, db_session):
    # merge_pdf_pages only ever reads real PDF pages - a cross-file cluster containing a
    # non-PDF source must fall back to one document per claim rather than attempt (and fail)
    # a cross-format merge.
    import fitz

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="mixedformat@example.com")
    login(client, op.email)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "scanned invoice")
    png_bytes = page.get_pixmap(dpi=100).tobytes("png")
    doc.close()

    claims = [[_claim(docs["Invoice"].id, [1])], [_claim(docs["Invoice"].id, [1])]]
    with _ocr("Invoice No: INV-0505", "Invoice No: INV-0505"), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims):
        resp = _upload(client, job.id, [
            ("inv.pdf", _pdf_bytes(1), "application/pdf"),
            ("inv_photo.png", png_bytes, "image/png"),
        ])

    assert resp.status_code == 200, resp.text
    rows = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id
    ).all()
    assert len(rows) == 2, "a non-PDF source in the cluster must not be force-merged"
