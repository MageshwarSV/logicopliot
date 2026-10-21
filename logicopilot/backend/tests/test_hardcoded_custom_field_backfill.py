"""A hardcoded, job-level custom field added to a template AFTER a job already exists had no
JobFieldValue row on that job at all - nothing ever created one until the job was re-extracted.
Found live: "ERP Consignee Name" (kind=hardcoded, ask_operator_required=True) was added to a
real template mid-session and never showed up on any job extracted before it existed, even
though it was meant to be mandatory on all of them.

_build_detail now backfills a missing hardcoded job-level field's JobFieldValue the first time
the job is read, with no extraction/OCR needed since the value is a pure constant."""
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import login, make_tenant, make_user


def test_a_hardcoded_field_added_after_the_job_existed_is_backfilled_on_read(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-BACKFILL1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # The custom field is created AFTER the job - exactly how "ERP Consignee Name" was added to
    # a live template with existing jobs already sitting on it.
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ERP Consignee Name",
                     kind="hardcoded", hardcoded_value="ACME IMPORTS PVT LTD",
                     ask_operator=True, ask_operator_required=True, per_row=False)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-backfill1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    values = {v["label_name"]: v for v in resp.json()["field_values"]}
    assert "ERP Consignee Name" in values
    assert values["ERP Consignee Name"]["value"] == "ACME IMPORTS PVT LTD"
    assert values["ERP Consignee Name"]["mandatory"] is True

    # It has to actually persist - not be recreated (with a new id) on every read, which would
    # silently discard any correction saved against it between two reads.
    first_id = values["ERP Consignee Name"]["id"]
    row = db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id,
                                                  JobFieldValue.custom_field_id == cf.id).all()
    assert len(row) == 1

    resp2 = client.get(f"/api/v1/jobs/{job.id}")
    values2 = {v["label_name"]: v for v in resp2.json()["field_values"]}
    assert values2["ERP Consignee Name"]["id"] == first_id


def test_a_per_row_hardcoded_field_is_not_backfilled_at_job_level(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-BACKFILL2", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # per_row=True means this field only ever makes sense keyed to a real product line, which
    # this job has none of yet - backfilling a job-level row for it would be wrong.
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Per Row Constant",
                     kind="hardcoded", hardcoded_value="X", ask_operator_required=True, per_row=True)
    db_session.add(cf)
    db_session.commit()

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-backfill2@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    labels = {v["label_name"] for v in resp.json()["field_values"]}
    assert "Per Row Constant" not in labels
