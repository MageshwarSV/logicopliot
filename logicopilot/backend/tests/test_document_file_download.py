"""Download original: a placed document's file as it arrived (an Excel stays an Excel)."""
import pytest
from fastapi import HTTPException

from app.api.v1 import jobs as jobs_api
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_original_excel_is_returned_as_is(db_session, tmp_path, monkeypatch):
    t = make_tenant(db_session)
    g = TemplateGroup(tenant_id=t.id, name="Sea", status="approved")
    db_session.add(g)
    db_session.commit()
    td = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Invoice", doc_type="Invoice")
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-XL", status="extracted")
    db_session.add_all([td, job])
    db_session.commit()
    f = tmp_path / "022G549774 INV PL.XLS"
    f.write_bytes(b"xls-bytes")
    jd = JobDocument(tenant_id=t.id, job_id=job.id, template_document_id=td.id, file_path=str(f),
                     page_count=0, source_kind="excel", sheet_name="Packing List",
                     original_name="022G549774 INV PL.XLS - Packing List")
    db_session.add(jd)
    db_session.commit()
    monkeypatch.setattr(jobs_api, "_load_job", lambda db, jid, scope, user=None: db.get(Job, jid))
    resp = jobs_api.download_document_file(job.id, jd.id, db_session, None, None)
    assert resp.path == str(f) and "022G549774%20INV%20PL.XLS" in resp.headers["content-disposition"]
    f.unlink()
    with pytest.raises(HTTPException):
        jobs_api.download_document_file(job.id, jd.id, db_session, None, None)
