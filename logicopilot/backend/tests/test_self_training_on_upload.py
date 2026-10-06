"""The self-training loop, exercised through the real HTTP endpoints.

The unit tests cover the store in isolation. This covers the thing that actually
has to work in production: an operator puts a file in a slot through the API, and
without anyone teaching it anything, that becomes an example the classifier is
shown next time.

Nothing is hardcoded and no keyword list is involved. The only thing that makes
this learn is an operator choosing a slot.
"""
from unittest.mock import patch

import fitz

from app.core import document_samples
from app.models.document_sample import DocumentSample
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_saying(text: str) -> bytes:
    """A PDF whose page really carries this text, so OCR has something to read."""
    doc = fitz.open()
    page = doc.new_page(width=420, height=600)
    y = 60
    for line in text.splitlines():
        page.insert_text((40, y), line, fontsize=11)
        y += 18
    return doc.tobytes()


INVOICE_PAGE = """ACME SHIPPING CO LTD
COMMERCIAL INVOICE
INVOICE NO: ACM-4417
UNIT PRICE  AMOUNT  TOTAL VALUE
TERMS OF PAYMENT 60 DAYS
"""


def _template(db, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db.add(group)
    db.commit()
    db.refresh(group)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice")
    pl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                          doc_type="PackingList", is_required=True)
    db.add_all([inv, pl])
    db.commit()
    db.refresh(inv)
    db.add(FieldMark(tenant_id=tenant.id, document_id=inv.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05))
    db.commit()
    return group, inv


def _job(db, tenant, group, tdoc, ref="JOB-LEARN"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, status="draft")
    db.add(job)
    db.commit()
    db.refresh(job)
    db.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path=None, page_count=0, file_index=0))
    db.commit()
    return job


# The OCR itself is Document AI's job and is covered elsewhere; what matters here
# is that the upload path reads the page and hands the text to the store.
FAKE_OCR = {"text": INVOICE_PAGE, "layout_text": INVOICE_PAGE,
            "tokens": [], "table_row_counts": [], "structured_text": None}


def test_putting_a_file_in_a_slot_teaches_the_classifier(client, db_session):
    """No one trains it. The operator files a document and that IS the training."""
    tenant = make_tenant(db_session)
    group, inv = _template(db_session, tenant)
    job = _job(db_session, tenant, group, inv)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-learn@example.com")
    login(client, op.email)

    assert db_session.query(DocumentSample).count() == 0

    with patch("app.api.v1.jobs.get_page_ocr", return_value=FAKE_OCR):
        resp = client.post(
            f"/api/v1/jobs/{job.id}/documents/{inv.id}/upload",
            files={"file": ("invoice.pdf", _pdf_saying(INVOICE_PAGE), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text

    learned = db_session.query(DocumentSample).all()
    assert len(learned) == 1, "the upload taught the classifier nothing"
    assert learned[0].template_document_id == inv.id
    # What was kept is the part that distinguishes an invoice from a packing list -
    # its field labels - not the letterhead both of them share.
    assert "unit price" in learned[0].excerpt
    assert "acme shipping" not in learned[0].excerpt


def test_what_was_learned_is_handed_to_the_classifier(client, db_session):
    """Stored is not enough; it has to reach the prompt."""
    tenant = make_tenant(db_session)
    group, inv = _template(db_session, tenant)
    job = _job(db_session, tenant, group, inv, ref="JOB-LEARN2")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-learn2@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs.get_page_ocr", return_value=FAKE_OCR):
        client.post(
            f"/api/v1/jobs/{job.id}/documents/{inv.id}/upload",
            files={"file": ("invoice.pdf", _pdf_saying(INVOICE_PAGE), "application/pdf")},
        )

    served = document_samples.for_slots(db_session, [inv.id])
    assert inv.id in served
    assert "unit price" in served[inv.id]


def test_an_upload_still_succeeds_when_learning_cannot(client, db_session):
    """The operator's file is already saved - a failure to learn must stay invisible."""
    tenant = make_tenant(db_session)
    group, inv = _template(db_session, tenant)
    job = _job(db_session, tenant, group, inv, ref="JOB-LEARN3")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-learn3@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=RuntimeError("OCR is down")):
        resp = client.post(
            f"/api/v1/jobs/{job.id}/documents/{inv.id}/upload",
            files={"file": ("invoice.pdf", _pdf_saying(INVOICE_PAGE), "application/pdf")},
        )

    assert resp.status_code == 200, "a failed sample took the upload down with it"
    uploaded = next(d for d in resp.json()["documents"]
                    if d["template_document_id"] == inv.id)
    assert uploaded["is_uploaded"] is True
    assert db_session.query(DocumentSample).count() == 0
