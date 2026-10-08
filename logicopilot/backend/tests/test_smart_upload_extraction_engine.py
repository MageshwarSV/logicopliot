"""smart_upload picks up the extraction_engine system setting and passes it through to
assign_documents_detailed - the default ("ocr_gpt4o_mini") must behave exactly as before
this setting existed, and flipping it to "gpt5_mini_vision" must reach the call with the
configured vision_engine_model."""
from unittest.mock import patch

import fitz

from app.core.system_settings import set_extraction_engine
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _setup_job(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Test Group", status="ready")
    db_session.add(group)
    db_session.flush()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice", order_index=0)
    db_session.add(tdoc)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ENGINEWIRE", status="extracted")
    db_session.add(job)
    db_session.flush()
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id,
                              template_document_id=tdoc.id, page_count=0))
    db_session.commit()
    return tenant, job, tdoc


def test_default_engine_reaches_assign_documents_detailed_unchanged(client, db_session):
    tenant, job, tdoc = _setup_job(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="eng-op1@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.api.v1.jobs.assign_documents_detailed",
               return_value=[[{"key": tdoc.id, "pages": [1], "evidence": "e"}]]) as mock_assign:
        resp = client.post(f"/api/v1/jobs/{job.id}/smart-upload",
                           files=[("files", ("inv.pdf", _pdf_bytes(), "application/pdf"))])

    assert resp.status_code == 200, resp.text
    assert mock_assign.call_args.kwargs["engine"] == "ocr_gpt4o_mini"


def test_vision_engine_setting_reaches_assign_documents_detailed_with_the_configured_model(client, db_session):
    tenant, job, tdoc = _setup_job(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="eng-op2@example.com")
    login(client, op.email)
    set_extraction_engine(db_session, "gpt5_mini_vision")

    with patch("app.api.v1.jobs.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.api.v1.jobs.assign_documents_detailed",
               return_value=[[{"key": tdoc.id, "pages": [1], "evidence": "e"}]]) as mock_assign:
        resp = client.post(f"/api/v1/jobs/{job.id}/smart-upload",
                           files=[("files", ("inv.pdf", _pdf_bytes(), "application/pdf"))])

    assert resp.status_code == 200, resp.text
    assert mock_assign.call_args.kwargs["engine"] == "gpt5_mini_vision"
    assert mock_assign.call_args.kwargs["vision_model"] == "gpt-5-mini"
