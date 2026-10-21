"""JobFieldValueOut.mandatory - found live: every Bill of Lading mark has ask_operator=False
(a document-read field, never an "ask the operator to type/confirm this" item), which
silently zeroed the EXISTING ask_operator_required (that one is ask_operator AND
ask_operator_required together - "of the asked fields, which ones actually block Submit
Entry", see required_ids in jobs.py) for every single one of them, regardless of each mark's
own real ask_operator_required column. Submit for Approval's own per-document mandatory check
(built against ask_operator_required, believing it reflected the raw column) never fired for
ANY Bill of Lading field as a result.

mandatory reads the raw column directly, independent of ask_operator - this is what fixes it."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import login, make_tenant, make_user


def test_a_mandatory_mark_with_ask_operator_false_still_reports_mandatory_true(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    # A plain document-read mark, exactly like every real Bill of Lading field today -
    # ask_operator=False (nothing to type/confirm - it's read straight off the page),
    # ask_operator_required=True (it's still mandatory in the "Submit for Approval must refuse
    # to go through blank" sense).
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="vessel_name",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                     ask_operator=False, ask_operator_required=True)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MANDATORY1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/bl.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, job_document_id=jd.id,
                       template_document_id=tdoc.id, label_name="vessel_name", extracted_value="")
    db_session.add(fv)
    db_session.commit()

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-mandatory1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    values = {v["label_name"]: v for v in resp.json()["field_values"]}
    assert values["vessel_name"]["ask_operator"] is False
    # The pre-existing, narrower flag stays correctly False - ask_operator being False always
    # zeroes it, by design, for the Final Submit "ask operator" popup's own purposes.
    assert values["vessel_name"]["ask_operator_required"] is False
    # The NEW, independent flag reports the raw column correctly regardless.
    assert values["vessel_name"]["mandatory"] is True


def test_an_optional_mark_with_ask_operator_false_reports_mandatory_false(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="optional_note",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                     ask_operator=False, ask_operator_required=False)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MANDATORY2", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/bl.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, job_document_id=jd.id,
                       template_document_id=tdoc.id, label_name="optional_note", extracted_value="")
    db_session.add(fv)
    db_session.commit()

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-mandatory2@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    values = {v["label_name"]: v for v in resp.json()["field_values"]}
    assert values["optional_note"]["mandatory"] is False


def test_a_mandatory_custom_field_with_ask_operator_false_reports_mandatory_true(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignment Type",
                     kind="ai", ai_prompt="x", ask_operator=False, ask_operator_required=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MANDATORY3", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                       label_name="Consignment Type", extracted_value="")
    db_session.add(fv)
    db_session.commit()

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-mandatory3@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    values = {v["label_name"]: v for v in resp.json()["field_values"]}
    assert values["Consignment Type"]["ask_operator_required"] is False
    assert values["Consignment Type"]["mandatory"] is True
