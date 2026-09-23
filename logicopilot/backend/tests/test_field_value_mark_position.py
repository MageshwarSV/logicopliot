"""JobFieldValueOut carries its mark's page/x/y/width/height straight off FieldMark, so the
frontend can highlight/scroll to the exact spot on the document preview when a field is
focused (Data Extraction screen)."""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_template_with_mark(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=2, x=0.15, y=0.25, width=0.3, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return group, tdoc, mark


def test_job_field_value_carries_its_marks_position(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template_with_mark(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MARKPOS", status="extracted")
    db_session.add(job)
    db_session.commit()
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                       template_document_id=tdoc.id, label_name="Invoice No",
                       extracted_value="EX123")
    db_session.add(fv)
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-markpos@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    row = next(v for v in resp.json()["field_values"] if v["label_name"] == "Invoice No")
    assert row["mark_page"] == 2
    assert row["mark_x"] == 0.15
    assert row["mark_y"] == 0.25
    assert row["mark_width"] == 0.3
    assert row["mark_height"] == 0.05


def test_custom_field_value_with_no_mark_reports_no_position(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-NOMARK", status="extracted")
    db_session.add(job)
    db_session.commit()
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=None,
                       label_name="Computed Field", extracted_value="X")
    db_session.add(fv)
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-nomark@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    row = next(v for v in resp.json()["field_values"] if v["label_name"] == "Computed Field")
    assert row["mark_page"] is None
    assert row["mark_x"] is None


def test_correct_field_value_response_also_carries_mark_position(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template_with_mark(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MARKPOS2", status="extracted")
    db_session.add(job)
    db_session.commit()
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                       template_document_id=tdoc.id, label_name="Invoice No",
                       extracted_value="EX123")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-markpos2@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "EX999"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mark_page"] == 2
    assert body["mark_width"] == 0.3
