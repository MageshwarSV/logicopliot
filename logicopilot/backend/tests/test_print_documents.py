"""Document Capture -> Print: the chosen documents, Unclassified pages and Excel files as one PDF,
in the order chosen. Offline."""
import io

import fitz
import openpyxl
import pytest
from fastapi import HTTPException

from app.api.v1 import jobs as jobs_api
from app.core.deps import TenantScope
from app.models.job import Job, JobDocument, UnclassifiedFile, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _png(path, text):
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 60), text)
    page.get_pixmap(dpi=150).save(str(path))
    doc.close()


def test_print_combines_the_chosen_documents_in_order(db_session, tmp_path, monkeypatch):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready")
    db_session.add(group)
    db_session.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="BL")
    db_session.add(awb)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PRINT", status="draft")
    db_session.add(job)
    db_session.flush()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=awb.id, page_count=2,
                     file_path=str(tmp_path / "orig.pdf"))
    db_session.add(jd)
    db_session.flush()
    monkeypatch.setattr(jobs_api, "_job_doc_dir", lambda _id: tmp_path / "doc")
    (tmp_path / "doc" / "pages").mkdir(parents=True)
    _png(tmp_path / "doc" / "pages" / "page_1.png", "AWB page 1")
    _png(tmp_path / "doc" / "pages" / "page_2.png", "AWB page 2")
    _png(tmp_path / "loose.png", "unclassified page")
    up = UnmatchedUploadPage(tenant_id=tenant.id, job_id=job.id, original_filename="HAWB A.pdf", page_number=1,
                             image_path=str(tmp_path / "loose.png"), ocr_text="")
    wb = openpyxl.Workbook()
    wb.active.title = "Detail"
    wb.active.append(["Invoice No", "INV-1"])
    buf = io.BytesIO()
    wb.save(buf)
    (tmp_path / "x.xlsx").write_bytes(buf.getvalue())
    uf = UnclassifiedFile(tenant_id=tenant.id, job_id=job.id, original_filename="x.xlsx", file_path=str(tmp_path / "x.xlsx"),
                          sheets=[{"name": "Detail", "rows": 1, "reason": "r"}])
    db_session.add_all([up, uf])
    db_session.commit()
    scope = TenantScope(tenant_id=tenant.id, is_super_admin=False)

    resp = jobs_api.print_job_documents(job.id, jobs_api.PrintDocumentsPayload(
        documents=[jd.id], unclassified_pages=[up.id], unclassified_files=[uf.id]),
        db=db_session, scope=scope, user=None)
    pdf = fitz.open(stream=resp.body, filetype="pdf")
    assert pdf.page_count == 4                                         # 2 AWB + 1 page + 1 sheet
    assert "INV-1" in pdf[3].get_text()

    # "Print pages...": only page 2 of the waybill, nothing else.
    resp = jobs_api.print_job_documents(job.id, jobs_api.PrintDocumentsPayload(
        documents=[jd.id], document_pages={jd.id: [2, 9]}), db=db_session, scope=scope, user=None)
    pdf = fitz.open(stream=resp.body, filetype="pdf")
    assert pdf.page_count == 1                                         # page 9 does not exist

    with pytest.raises(HTTPException):
        jobs_api.print_job_documents(job.id, jobs_api.PrintDocumentsPayload(), db=db_session, scope=scope, user=None)
