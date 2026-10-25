"""Word/Excel files uploaded through Smart Upload are converted to PDF before classification
ever sees them (see app.core.office_convert). The actual LibreOffice conversion needs the
`soffice` binary, which this suite's own environment does not carry - so convert_word_to_pdf/
convert_excel_sheets_to_pdfs are mocked here (same reasoning test_smart_upload.py already
mocks OCR/classification), while everything AROUND them - which files get sent for
conversion, how many classification entries a workbook turns into, what happens when
conversion fails - is exercised for real."""

from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from app.models.job import JobDocument
from tests.conftest import login, make_user
from tests.test_smart_upload import _claim, _mock_ocr, _setup_job, _upload


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _mock_word_conversion(tmp_path, text="converted word doc"):
    pdf_path = tmp_path / "word.pdf"
    pdf_path.write_bytes(_pdf_bytes(text))
    return patch("app.core.office_convert.convert_word_to_pdf", return_value=pdf_path)


def _mock_excel_conversion(tmp_path, sheets: dict[str, str]):
    results = []
    for sheet_name, text in sheets.items():
        p = tmp_path / f"{sheet_name}.pdf"
        p.write_bytes(_pdf_bytes(text))
        results.append((sheet_name, p))
    return patch("app.core.office_convert.convert_excel_sheets_to_pdfs", return_value=results)


def test_word_document_is_converted_then_classified(client, db_session, tmp_path):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="word-conv@example.com")
    login(client, op.email)

    with _mock_ocr(), _mock_word_conversion(tmp_path), \
         patch("app.api.v1.jobs.assign_documents_detailed",
               return_value=[[_claim(docs["Invoice"].id, [1])]]) as mock_classify:
        resp = _upload(client, job.id, [
            ("quotation.docx", b"not a real docx - never read, conversion is mocked",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ])

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["matched"] == ["Invoice"]
    # Classification saw exactly ONE prepared file - the converted PDF, not the raw docx bytes
    # (fitz.open on those would have raised and produced an "unreadable" error result instead).
    assert mock_classify.call_count == 1
    prepared = mock_classify.call_args.args[0]
    assert len(prepared) == 1
    assert prepared[0]["name"] == "quotation.pdf"


def test_excel_workbook_splits_into_one_classification_candidate_per_sheet(client, db_session, tmp_path):
    tenant, job, docs = _setup_job(
        db_session, doc_specs=[("Invoice", "Invoice"), ("PackingList", "PackingList")])
    op = make_user(db_session, role="operator", tenant=tenant, email="excel-conv@example.com")
    login(client, op.email)

    claims = [
        [_claim(docs["Invoice"].id, [1])],
        [_claim(docs["PackingList"].id, [1])],
    ]
    with _mock_ocr(), \
         _mock_excel_conversion(tmp_path, {"Invoice": "INVOICE SHEET", "Packing List": "PACKING LIST SHEET"}), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=claims) as mock_classify:
        resp = _upload(client, job.id, [
            ("shipment.xlsx", b"not a real xlsx - never read, conversion is mocked",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Two results - one per worksheet, named after the original file + sheet, not the
    # workbook's own filename (there is no single "the file" result for a multi-sheet book).
    assert len(body["results"]) == 2
    assert {r["matched"][0] for r in body["results"]} == {"Invoice", "PackingList"}
    assert all("shipment" in r["filename"] for r in body["results"])
    assert mock_classify.call_count == 1

    inv_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Invoice"].id).one()
    pl_jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["PackingList"].id).one()
    assert inv_jd.file_path is not None and pl_jd.file_path is not None
    assert inv_jd.file_path != pl_jd.file_path


def test_conversion_failure_is_reported_not_silently_dropped(client, db_session):
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="word-fail@example.com")
    login(client, op.email)

    from app.core.office_convert import OfficeConversionFailed

    with _mock_ocr(), \
         patch("app.core.office_convert.convert_word_to_pdf",
               side_effect=OfficeConversionFailed("could not read this file")), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=[]) as mock_classify:
        resp = _upload(client, job.id, [
            ("broken.docx", b"garbage", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any("could not read this file" in r.get("error", "") for r in body["results"])
    # Nothing left to classify - the one file that was uploaded failed to convert, and
    # assign_documents_detailed is only ever called when there is something prepared.
    assert mock_classify.call_count == 0


def test_zip_with_a_docx_now_attempts_conversion_instead_of_being_silently_dropped(client, db_session):
    # Before this feature, a .docx inside a zip was simply never extracted at all - a zip
    # containing ONLY a .docx looked identical to an empty zip. Now it's a real upload attempt:
    # a garbage (non-real) .docx correctly fails conversion with a reported error, rather than
    # the whole request silently reporting zero files.
    import io
    import zipfile

    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="zipdocx@example.com")
    login(client, op.email)

    from app.core.office_convert import OfficeConversionFailed

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notes.docx", b"not a real docx")
    with patch("app.core.office_convert.convert_word_to_pdf",
               side_effect=OfficeConversionFailed("could not read this file")):
        resp = _upload(client, job.id, [("junk.zip", buf.getvalue(), "application/zip")])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["filename"] == "notes.docx"
    assert "could not read this file" in body["results"][0]["error"]


def test_genuinely_unsupported_extension_is_untouched_by_the_office_conversion_path(client, db_session):
    # A file this feature has no business touching (e.g. .csv) must come out exactly as before
    # this change: silently not matched, same as any other extension CLASSIFY_EXTS has never
    # accepted - this feature only widens the gate for Word/Excel, nothing else.
    tenant, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="unsupported@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed") as mock_classify:
        resp = _upload(client, job.id, [("data.csv", b"a,b,c\n1,2,3", "text/csv")])

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []
    mock_classify.assert_not_called()
