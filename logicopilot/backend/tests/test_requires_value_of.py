""""Fill only when <another field> has a value" - CustomField.requires_value_of.

MAWB Date requires MAWB Number: a plain House Air Waybill has no master waybill, so it has no
master date either, but the two are read off the document separately and a date could come back
on a page with no master number - the house date, in the wrong box.
"""
from app.api.v1.jobs import _enforce_requires_value_of
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session, number="020-12345678", date="19-Oct-2026"):
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-mawb@example.com")
    login(client, "sa-mawb@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready")
    db_session.add(group)
    db_session.flush()
    num_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="MAWB Number", kind="ai")
    date_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="MAWB Date", kind="ai",
                          requires_value_of="MAWB Number")
    db_session.add_all([num_cf, date_cf])
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MAWB", status="extracted")
    db_session.add(job)
    db_session.flush()
    num = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=num_cf.id,
                        label_name="MAWB Number", extracted_value=number)
    dt = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=date_cf.id,
                       label_name="MAWB Date", extracted_value=date)
    db_session.add_all([num, dt])
    db_session.commit()
    return job, num, dt, group, date_cf


def _val(db_session, fv):
    db_session.expire_all()
    f = db_session.get(JobFieldValue, fv.id)
    return (f.corrected_value if f.corrected_value is not None else f.extracted_value) or ""


def test_no_number_after_extraction_means_no_date(client, db_session):
    job, num, dt, *_ = _setup(client, db_session, number="")
    assert _enforce_requires_value_of(db_session, job) == ["MAWB Date"]
    db_session.commit()
    assert _val(db_session, dt) == ""


def test_a_number_keeps_its_date(client, db_session):
    job, num, dt, *_ = _setup(client, db_session)
    assert _enforce_requires_value_of(db_session, job) == []
    assert _val(db_session, dt) == "19-Oct-2026"


def test_clearing_the_number_on_screen_clears_the_date(client, db_session):
    job, num, dt, *_ = _setup(client, db_session)
    r = client.patch(f"/api/v1/job-field-values/{num.id}", json={"corrected_value": ""})
    assert r.status_code == 200, r.text
    assert _val(db_session, dt) == ""


def test_typing_a_date_without_a_number_is_refused_with_a_reason(client, db_session):
    job, num, dt, *_ = _setup(client, db_session, number="", date="")
    r = client.patch(f"/api/v1/job-field-values/{dt.id}", json={"corrected_value": "19-Oct-2026"})
    assert r.status_code == 400
    assert "MAWB Number" in r.json()["detail"]
    assert _val(db_session, dt) == ""


def test_typing_a_date_once_the_number_is_there_is_fine(client, db_session):
    job, num, dt, *_ = _setup(client, db_session, number="", date="")
    client.patch(f"/api/v1/job-field-values/{num.id}", json={"corrected_value": "020-12345678"})
    r = client.patch(f"/api/v1/job-field-values/{dt.id}", json={"corrected_value": "19-Oct-2026"})
    assert r.status_code == 200, r.text
    assert _val(db_session, dt) == "19-Oct-2026"


def test_the_setting_must_name_a_real_field_on_the_template(client, db_session):
    job, num, dt, group, date_cf = _setup(client, db_session)
    bad = client.patch(f"/api/v1/custom-fields/{date_cf.id}", json={"requires_value_of": "MAWB Nmber"})
    assert bad.status_code == 400
    ok = client.patch(f"/api/v1/custom-fields/{date_cf.id}", json={"requires_value_of": " MAWB Number "})
    assert ok.status_code == 200, ok.text
    assert ok.json()["requires_value_of"] == "MAWB Number"
    off = client.patch(f"/api/v1/custom-fields/{date_cf.id}", json={"requires_value_of": ""})
    assert off.json()["requires_value_of"] is None
