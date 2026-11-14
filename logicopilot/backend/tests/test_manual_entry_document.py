"""A document with no file that the template lets be entered by hand.

November 5's Air Waybill is optional, but the entry still needs its values when the customer
never sends one. With TemplateDocument.manual_entry_when_missing set, a job with no Air Waybill
file still gets the tab: an empty box for every job-level mark, typeable, never mandatory.
"""
from app.api.v1.jobs import _ensure_manual_entry_fields
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session, manual=True, awb_file=None):
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-manual@example.com")
    login(client, "sa-manual@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved")
    db_session.add(group)
    db_session.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="BL",
                           order_index=1, is_required=False, manual_entry_when_missing=manual)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice",
                           order_index=2)
    db_session.add_all([awb, inv])
    db_session.flush()

    def mark(doc, label, **kw):
        m = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name=label,
                      x=0.1, y=0.1, width=0.1, height=0.02, **kw)
        db_session.add(m)
        return m

    marks = [mark(awb, "HBL No", ask_operator_required=True), mark(awb, "vessel_name"),
             mark(awb, "Gross Wt", ask_operator_required=True),
             mark(awb, "container_number", is_multi_value=True)]
    mark(inv, "Invoice No")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MANUAL", status="extracted")
    db_session.add(job)
    db_session.flush()
    awb_slot = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=awb.id,
                           file_path=awb_file)
    inv_slot = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                           file_path="x/invoice.pdf", page_count=1)
    db_session.add_all([awb_slot, inv_slot])
    db_session.commit()
    return job, group, awb, awb_slot, marks


def _awb_fields(db_session, job, slot):
    db_session.expire_all()
    return {f.label_name: f for f in db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.job_document_id == slot.id).all()}


def test_a_missing_air_waybill_gets_an_empty_box_for_every_job_level_mark(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session)
    assert _ensure_manual_entry_fields(db_session, job, group) == 3
    db_session.commit()
    fields = _awb_fields(db_session, job, slot)
    assert set(fields) == {"HBL No", "vessel_name", "Gross Wt"}      # the table column is skipped
    assert all((f.extracted_value or "") == "" for f in fields.values())


def test_it_never_duplicates_or_overwrites_what_was_typed(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session)
    _ensure_manual_entry_fields(db_session, job, group)
    db_session.commit()
    hbl = _awb_fields(db_session, job, slot)["HBL No"]
    r = client.patch(f"/api/v1/job-field-values/{hbl.id}", json={"corrected_value": "22H0523013"})
    assert r.status_code == 200, r.text
    assert _ensure_manual_entry_fields(db_session, job, group) == 0
    assert _awb_fields(db_session, job, slot)["HBL No"].corrected_value == "22H0523013"


def test_the_job_shows_the_tab_and_nothing_on_it_is_mandatory(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/manual-entry-fields")
    assert r.status_code == 200, r.text
    d = r.json()
    doc = next(x for x in d["documents"] if x["id"] == slot.id)
    assert doc["is_uploaded"] is False and doc["manual_entry"] is True
    mine = [f for f in d["field_values"] if f["job_document_id"] == slot.id]
    assert {f["label_name"] for f in mine} == {"HBL No", "vessel_name", "Gross Wt"}
    assert all(f["mandatory"] is False for f in mine)


def test_an_uploaded_air_waybill_is_not_a_hand_entry_tab(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session, awb_file="x/awb.pdf")
    assert _ensure_manual_entry_fields(db_session, job, group) == 0
    d = client.get(f"/api/v1/jobs/{job.id}").json()
    assert next(x for x in d["documents"] if x["id"] == slot.id)["manual_entry"] is False


def test_a_document_without_the_setting_gets_no_tab(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session, manual=False)
    assert _ensure_manual_entry_fields(db_session, job, group) == 0
    d = client.get(f"/api/v1/jobs/{job.id}").json()
    assert next(x for x in d["documents"] if x["id"] == slot.id)["manual_entry"] is False


def test_the_setting_is_switched_through_the_api(client, db_session):
    job, group, awb, slot, _ = _setup(client, db_session, manual=False)
    r = client.patch(f"/api/v1/template-documents/{awb.id}/manual-entry",
                     json={"manual_entry_when_missing": True})
    assert r.status_code == 200, r.text
    assert r.json()["manual_entry_when_missing"] is True
    assert client.post(f"/api/v1/jobs/{job.id}/manual-entry-fields").status_code == 200
    assert len(_awb_fields(db_session, job, slot)) == 3
