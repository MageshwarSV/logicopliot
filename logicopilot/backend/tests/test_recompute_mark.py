"""POST /jobs/{job_id}/marks/{mark_id}/recompute — backfilling one mark's value on an
already-extracted job after its prompt was tightened, without re-running extraction (which
would rewrite every other field and reset every approval)."""
from unittest.mock import patch

from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="approved", mode="Sea Export")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="EXPORTER",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return group, tdoc, mark


def _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-MARKBACKFILL"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted",
             validation_approved=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    return job, jd


def _ocr_patch(text="Exporter:- Real Exporter Co"):
    return patch("app.api.v1.jobs.get_page_ocr",
                return_value={"layout_text": text, "text": text, "tokens": []})


def test_backfills_a_mark_from_its_own_uploaded_document(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 template_document_id=tdoc.id, job_document_id=jd.id,
                                 label_name="EXPORTER", extracted_value="4S Logistics"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields",
                             return_value={"EXPORTER": "Real Exporter Co"}):
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["extracted_value"] == "Real Exporter Co"
    assert body[0]["is_custom"] is False


def test_one_result_per_uploaded_file_in_the_slot(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd1 = _make_extracted_job(db_session, tenant, group, tdoc)
    jd2 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                      file_path="fake/second.pdf", page_count=1, file_index=1)
    db_session.add(jd2)
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa2@example.com")
    login(client, "sa2@example.com")

    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields",
                             return_value={"EXPORTER": "Some Co"}):
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_second_call_updates_the_same_row(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)

    make_user(db_session, role=SUPER_ADMIN, email="sa3@example.com")
    login(client, "sa3@example.com")

    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields", return_value={"EXPORTER": "First"}):
        client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields", return_value={"EXPORTER": "Second"}):
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")

    assert resp.json()[0]["extracted_value"] == "Second"
    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id).all())
    assert len(rows) == 1
    assert rows[0].extracted_value == "Second"


def test_nothing_else_on_the_job_is_touched(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    other_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                           page_number=1, x=0.5, y=0.1, width=0.2, height=0.05)
    db_session.add(other_mark)
    db_session.commit()
    db_session.refresh(other_mark)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=other_mark.id,
                                 template_document_id=tdoc.id, job_document_id=jd.id,
                                 label_name="Invoice No", extracted_value="INV-999"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa4@example.com")
    login(client, "sa4@example.com")

    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields",
                             return_value={"EXPORTER": "Real Exporter Co"}):
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 200

    db_session.refresh(job)
    assert job.status == "extracted"
    assert job.validation_approved is True
    untouched = (db_session.query(JobFieldValue)
                 .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == other_mark.id).first())
    assert untouched.extracted_value == "INV-999"


def test_renaming_the_mark_after_a_row_exists_updates_the_label_on_recompute(client, db_session):
    """Same bug as the custom-field recompute endpoint, found the same way: the existing
    JobFieldValue row's label_name was only ever set at creation, never re-synced."""
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 template_document_id=tdoc.id, job_document_id=jd.id,
                                 label_name="EXPORTER", extracted_value="Old Value"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-rename@example.com")
    login(client, "sa-rename@example.com")

    mark.label_name = "Exporter Renamed"
    db_session.commit()

    with _ocr_patch(), patch("app.api.v1.jobs.extract_document_fields",
                             return_value={"Exporter Renamed": "New Value"}):
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["label_name"] == "Exporter Renamed"
    row = (db_session.query(JobFieldValue)
           .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id).one())
    assert row.label_name == "Exporter Renamed"


def test_rejects_a_multi_value_mark(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, _ = _make_template(db_session, tenant)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Description",
                         page_number=1, x=0.1, y=0.5, width=0.2, height=0.05, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    db_session.refresh(row_mark)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)

    make_user(db_session, role=SUPER_ADMIN, email="sa5@example.com")
    login(client, "sa5@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/marks/{row_mark.id}/recompute")
    assert resp.status_code == 400


def test_rejects_a_mark_from_a_different_template(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)

    other_group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(other_group)
    db_session.commit()
    db_session.refresh(other_group)
    other_tdoc = TemplateDocument(tenant_id=tenant.id, group_id=other_group.id, name="Invoice", doc_type="invoice")
    db_session.add(other_tdoc)
    db_session.commit()
    db_session.refresh(other_tdoc)
    foreign_mark = FieldMark(tenant_id=tenant.id, document_id=other_tdoc.id, label_name="shipper",
                             page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(foreign_mark)
    db_session.commit()
    db_session.refresh(foreign_mark)

    make_user(db_session, role=SUPER_ADMIN, email="sa6@example.com")
    login(client, "sa6@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/marks/{foreign_mark.id}/recompute")
    assert resp.status_code == 404


def test_no_uploaded_document_in_the_slot(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-NOFILE", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path=None, page_count=0)
    db_session.add(jd)
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa7@example.com")
    login(client, "sa7@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 400


def test_tenant_admin_cannot_call_it(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")
    assert resp.status_code == 403
