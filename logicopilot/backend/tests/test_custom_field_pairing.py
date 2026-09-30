"""CustomField.paired_custom_field_id / picker_heading / sync_field_ids - lets any two
already-configured custom fields (each still computed exactly as its own kind already does -
hardcoded/ai/lookup, unchanged) be linked into a picker pair. Deliberately NOT a new
CustomField.kind: the pairing is pure metadata, denormalized onto every JobFieldValue of the
field it's set on (GET /jobs/{id}) so the frontend can render the picker without a second
lookup, matched by custom_field_id (never by label text, which a Super Admin can name
however they like)."""
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


def _make_job(db_session, tenant, group, tdoc, reference="JOB-PAIR1"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job


def test_paired_field_carries_pairing_config_on_its_job_field_values(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)

    option_b = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                           kind="ai", ai_prompt="read off a document")
    db_session.add(option_b)
    db_session.commit()
    db_session.refresh(option_b)

    ritc = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="RITC No.", kind="lookup")
    db_session.add(ritc)
    db_session.commit()
    db_session.refresh(ritc)

    option_a = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number", kind="lookup",
        paired_custom_field_id=option_b.id,
        picker_heading="CTH — pick which one is right for this line",
        sync_field_ids=[ritc.id],
    )
    db_session.add(option_a)
    db_session.commit()
    db_session.refresh(option_a)

    for cf, value in ((option_a, "39269099"), (option_b, "3923900000"), (ritc, "39269099")):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                     label_name=cf.label_name, extracted_value=value, row_index=1))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-pair1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}

    primary = by_label["Dump CTH Number"]
    assert primary["custom_field_id"] == option_a.id
    assert primary["paired_custom_field_id"] == option_b.id
    assert primary["picker_heading"] == "CTH — pick which one is right for this line"
    assert primary["sync_field_ids"] == [ritc.id]

    # The OTHER half of the pair carries no pairing config of its own - the picker is
    # configured on option_a only, not mutual.
    other = by_label["Document CTH"]
    assert other["custom_field_id"] == option_b.id
    assert other["paired_custom_field_id"] is None


def test_an_unpaired_field_has_null_pairing_config(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc, reference="JOB-PAIR2")

    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee", kind="ai",
                     ai_prompt="read the consignee")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name, extracted_value="ACME"))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-pair2@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    fv = next(f for f in resp.json()["field_values"] if f["label_name"] == "Consignee")
    assert fv["paired_custom_field_id"] is None
    assert fv["picker_heading"] is None
    assert fv["sync_field_ids"] is None


def test_edit_endpoint_can_set_and_clear_the_pairing(client, db_session):
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    a = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="A", kind="hardcoded",
                    hardcoded_value="x")
    b = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="B", kind="hardcoded",
                    hardcoded_value="y")
    db_session.add_all([a, b])
    db_session.commit()
    db_session.refresh(a)
    db_session.refresh(b)

    make_user(db_session, role="super_admin", email="sa-pair@example.com")
    login(client, "sa-pair@example.com")

    resp = client.patch(f"/api/v1/custom-fields/{a.id}", json={
        "paired_custom_field_id": b.id,
        "picker_heading": "Pick one",
        "sync_field_ids": [],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["paired_custom_field_id"] == b.id
    assert resp.json()["picker_heading"] == "Pick one"

    resp = client.patch(f"/api/v1/custom-fields/{a.id}", json={"paired_custom_field_id": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["paired_custom_field_id"] is None
