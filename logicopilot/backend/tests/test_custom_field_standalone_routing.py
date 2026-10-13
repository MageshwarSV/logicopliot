"""A lookup CustomField keyed on a standalone mark's own label (container_type, say) belongs
on THAT mark's own card (Container Detail) rather than the product line-item cards -
JobFieldValueOut.standalone_multi_value is derived from CustomField.lookup_key_label matching
a standalone FieldMark, the same screen-placement signal an AI field ticked
multi_value_from_document already carries, so the frontend can route it correctly with no
second lookup of its own."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="container_type",
                             page_number=1, x=0.1, y=0.1, width=0.2, height=0.05,
                             is_multi_value=True, standalone_multi_value=True))
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-STANDALONE1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job


def test_a_lookup_field_keyed_on_a_standalone_mark_is_flagged_standalone(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)

    container_code = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="ContainerTypeCode", kind="lookup",
        lookup_key_label="container_type", per_row=True,
    )
    db_session.add(container_code)
    db_session.commit()
    db_session.refresh(container_code)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=container_code.id,
                                 label_name=container_code.label_name, extracted_value="45G0", row_index=1))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-standalone1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}
    assert by_label["ContainerTypeCode"]["standalone_multi_value"] is True


def test_a_lookup_field_keyed_on_a_product_field_is_not_flagged_standalone(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)

    cth = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number", kind="lookup",
        lookup_key_label="product_description", per_row=True,
    )
    db_session.add(cth)
    db_session.commit()
    db_session.refresh(cth)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                                 label_name=cth.label_name, extracted_value="39269099", row_index=1))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-standalone2@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}
    assert by_label["Dump CTH Number"]["standalone_multi_value"] is False
