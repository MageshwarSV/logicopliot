"""JobDocumentOut.updated_at - a same-slot delete+reupload (the common case: a document type
with only one file) reuses the SAME JobDocument row/id (see _remove_document_file's "keep the
slot itself, empty" branch), and the new file often has the same page_count too. Without a
signal that changes across that cycle, the frontend's document viewer had no way to tell "the
file actually changed" from "nothing happened" and kept showing the page images it had already
fetched for the file that was just deleted - a real bug reported live. This is covered at the
unit level here; the browser-side fix (re-fetching the viewer's images when updated_at
changes, and cache-busting the request) lives in JobRunPage.tsx."""
import fitz
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _tiny_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    # A second REQUIRED document that is never uploaded - Document Capture never completes,
    # so _maybe_auto_extract never fires and the job stays "draft" for this test's whole
    # delete+reupload cycle instead of racing a background extraction thread.
    other_tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                                  doc_type="packing_list", is_required=True)
    db_session.add(other_tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REUP", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    slot = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path=None, page_count=0, file_index=0)
    db_session.add(slot)
    db_session.commit()
    return job


def test_reupload_into_the_same_slot_bumps_updated_at_with_id_and_page_count_unchanged(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-reup@example.com")
    login(client, op.email)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    before = next(d for d in resp.json()["documents"] if d["template_document_id"] == tdoc.id)
    assert before["is_uploaded"] is True

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{before['id']}/file")
    assert resp.status_code == 200, resp.text

    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice-corrected.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    after = next(d for d in resp.json()["documents"] if d["template_document_id"] == tdoc.id)

    # The common case this bug lived in: same slot reused, same id, same page count - the
    # ONLY thing that tells the frontend the file is actually different is updated_at.
    assert after["id"] == before["id"]
    assert after["page_count"] == before["page_count"]
    assert after["updated_at"] != before["updated_at"]
