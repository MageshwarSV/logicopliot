"""PATCH /job-field-values/{value_id} (correct_field_value) - found live: a CTH/RITC value
that looked identical on screen failed the ERP's own upload when the system had written it,
but succeeded once an operator cleared the cell and retyped the exact same text by hand.

The cause: this was the one write site for corrected_value with no .strip() at all, while
nearly every READ site elsewhere in this file already defensively strips
(corrected_value or extracted_value or "").strip() before using a value. A stray leading/
trailing space or non-breaking space copy-pasted into the box rode uncleaned straight through
to the Excel export (entry_values_and_rows reads fv.value, which prefers corrected_value) and
on into the ERP's own upload - while retyping by hand never introduces that character in the
first place. Fixed by stripping on the way IN here too, matching every read site's own
convention instead of only ever cleaning up after the fact."""
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_job_with_value(db_session, tenant, extracted_value=""):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-STRIP", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="Dump CTH Number",
                       extracted_value=extracted_value)
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    return job, fv


def test_a_trailing_space_copy_pasted_into_the_box_is_stripped_on_save(client, db_session):
    tenant = make_tenant(db_session)
    job, fv = _make_job_with_value(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-strip1@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "39269099 "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["corrected_value"] == "39269099"
    assert resp.json()["value"] == "39269099"

    db_session.refresh(fv)
    assert fv.corrected_value == "39269099"


def test_a_leading_non_breaking_space_is_stripped_on_save(client, db_session):
    """Python's str.strip() treats a non-breaking space (U+00A0) as whitespace too - this is
    exactly the kind of invisible character a copy-paste from a PDF or another spreadsheet
    can carry in, looking identical to a clean value on screen."""
    tenant = make_tenant(db_session)
    job, fv = _make_job_with_value(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-strip2@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "\xa039269099"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["corrected_value"] == "39269099"


def test_a_clean_value_is_unaffected(client, db_session):
    tenant = make_tenant(db_session)
    job, fv = _make_job_with_value(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-strip3@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "39269099"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["corrected_value"] == "39269099"


def test_a_whitespace_only_correction_is_stored_as_empty(client, db_session):
    tenant = make_tenant(db_session)
    job, fv = _make_job_with_value(db_session, tenant, extracted_value="old value")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-strip4@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "   "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["corrected_value"] == ""
