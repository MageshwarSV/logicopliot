"""A page smart-upload could not confidently place used to be silently discarded along with
the temp upload dir - gone the instant the request finished, with no trace it ever existed.
Now it's kept as an UnmatchedUploadPage row an operator can resolve by hand (GET the list,
POST assign onto a real document slot). That manual correction is remembered the same way an
ordinary manual upload already teaches the classifier - via document_samples.remember(), see
test_document_samples.py/test_self_training_on_upload.py for that mechanism's own coverage;
this file only checks that assigning an UNCLASSIFIED page reaches the same hook."""
from unittest.mock import patch

import fitz

from app.models.document_sample import DocumentSample
from app.models.job import Job, JobDocument, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

# Realistic enough to survive document_samples._trim/_label_lines (several genuine
# field-label-style lines, no sender-identifying text) - a bare "some text" fixture
# produces no excerpt at all and would make the learning assertion vacuous.
_REALISTIC_OCR_TEXT = (
    "ARRIVAL NOTICE\n"
    "Consignee Notify Party\n"
    "Port of Discharge Final Destination\n"
    "Vessel Voyage Number\n"
    "Container Number Seal Number\n"
    "Gross Weight Net Weight Measurement\n"
    "Freight Prepaid Collect\n"
)


def _pdf_bytes(n_pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _setup_job(db_session, *, doc_specs):
    tenant = make_tenant(db_session)
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
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-UNCLASSIFIED", status="extracted")
    db_session.add(job)
    db_session.flush()
    for d in docs.values():
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id,
                                   template_document_id=d.id, page_count=0))
    db_session.commit()
    return tenant, group, job, docs


def _upload(client, job_id, files):
    return client.post(f"/api/v1/jobs/{job_id}/smart-upload", files=[("files", f) for f in files])


def _mock_ocr():
    return patch("app.api.v1.jobs.ocr_page_image", return_value={"text": "some text", "tokens": []})


def test_a_page_matching_nothing_is_kept_as_unclassified_not_discarded(client, db_session):
    tenant, group, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="unc1@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed", return_value=[[]]):
        resp = _upload(client, job.id, [("arrival_notice.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 200, resp.text

    rows = db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job.id).all()
    assert len(rows) == 1
    assert rows[0].original_filename == "arrival_notice.pdf"
    assert rows[0].page_number == 1
    assert rows[0].ocr_text.strip()

    list_resp = client.get(f"/api/v1/jobs/{job.id}/unclassified-pages")
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    assert len(body) == 1
    assert body[0]["original_filename"] == "arrival_notice.pdf"
    assert body[0]["page_number"] == 1


def test_a_confidently_matched_page_does_not_stay_unclassified(client, db_session):
    tenant, group, job, docs = _setup_job(db_session, doc_specs=[("Invoice", "Invoice")])
    op = make_user(db_session, role="operator", tenant=tenant, email="unc2@example.com")
    login(client, op.email)

    with _mock_ocr(), patch("app.api.v1.jobs.assign_documents_detailed",
                             return_value=[[{"key": docs["Invoice"].id, "pages": [1], "evidence": "e"}]]):
        resp = _upload(client, job.id, [("inv.pdf", _pdf_bytes(1), "application/pdf")])
    assert resp.status_code == 200, resp.text

    rows = db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job.id).all()
    assert rows == []


def test_assigning_an_unclassified_page_writes_it_into_the_slot_and_removes_the_staging_row(
    client, db_session,
):
    tenant, group, job, docs = _setup_job(db_session, doc_specs=[("Freight Certificate", "Custom")])
    op = make_user(db_session, role="operator", tenant=tenant, email="unc3@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs.ocr_page_image",
               return_value={"text": _REALISTIC_OCR_TEXT, "tokens": []}), \
         patch("app.api.v1.jobs.assign_documents_detailed", return_value=[[]]):
        _upload(client, job.id, [("arrival_notice.pdf", _pdf_bytes(1), "application/pdf")])
    page = db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job.id).one()

    resp = client.post(
        f"/api/v1/jobs/{job.id}/unclassified-pages/{page.id}/assign",
        json={"template_document_id": docs["Freight Certificate"].id},
    )
    assert resp.status_code == 200, resp.text

    assert db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.id == page.id).first() is None
    jd = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.template_document_id == docs["Freight Certificate"].id
    ).one()
    assert jd.file_path is not None
    assert jd.page_count == 1

    # Assigning an unclassified page teaches the classifier the same way an ordinary
    # manual upload does - see document_samples.remember(), called identically from both.
    sample = db_session.query(DocumentSample).filter(
        DocumentSample.template_document_id == docs["Freight Certificate"].id
    ).one()
    assert sample.job_document_id == jd.id
    assert sample.excerpt.strip()
