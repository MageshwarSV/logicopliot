"""CustomField.hidden_from_capture - a computed/looked-up field (e.g. ContainerTypeCode,
built purely to feed an ERP script's own field mapping) that must still write a real
JobFieldValue on every row exactly as normal, but never surface on the job screen itself.
Denormalized onto every JobFieldValue of the field it's set on (GET /jobs/{id}), the same
way paired_custom_field_id/standalone_multi_value already are - see
test_custom_field_pairing.py for that established shape."""
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc, reference="JOB-HIDE1"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job


def test_a_hidden_field_carries_the_flag_on_its_job_field_values(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)

    container_type = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="ContainerTypeCode", kind="lookup",
        per_row=True, skip_material_master=True, hidden_from_capture=True,
    )
    db_session.add(container_type)
    db_session.commit()
    db_session.refresh(container_type)

    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, custom_field_id=container_type.id,
        label_name=container_type.label_name, extracted_value="40HC", row_index=1,
    ))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-hide1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}

    assert by_label["ContainerTypeCode"]["hidden_from_capture"] is True


def test_an_ordinary_field_has_hidden_from_capture_false(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc, reference="JOB-HIDE2")

    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                      kind="lookup", per_row=True)
    db_session.add(cth)
    db_session.commit()
    db_session.refresh(cth)

    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
        label_name=cth.label_name, extracted_value="39269099", row_index=1,
    ))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-hide2@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}

    assert by_label["Dump CTH Number"]["hidden_from_capture"] is False


def test_create_and_edit_custom_field_round_trip_the_flag(client, db_session):
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-hide1@example.com")
    login(client, admin.email)

    resp = client.post(
        f"/api/v1/template-groups/{group.id}/custom-fields",
        json={"label_name": "ContainerTypeCode", "kind": "lookup", "per_row": True,
              "hidden_from_capture": True},
    )
    assert resp.status_code == 201, resp.text
    field_id = resp.json()["id"]
    assert resp.json()["hidden_from_capture"] is True

    resp = client.patch(f"/api/v1/custom-fields/{field_id}", json={"hidden_from_capture": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["hidden_from_capture"] is False
