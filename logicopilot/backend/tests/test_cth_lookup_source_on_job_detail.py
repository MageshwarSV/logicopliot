"""JobFieldValueOut.lookup_source - the "Part Code" / "Description" note shown beside a
looked-up CTH, so an operator can see WHICH key of the customer's sheet produced it.

Worked out when the job is read rather than stored on the row, so it appears on jobs that were
extracted long before this existed. The thing that makes that honest is the guard tested here:
it is reported ONLY when re-running the sheet lookup reproduces the exact value on screen. A
code the operator typed themselves, or one that came from the learned-reference tier rather
than the sheet, must say nothing at all instead of claiming a source it never came from.
"""
import pytest

from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

MASTER = {"PART-ONE": "87089900", "A WIDGET OF SORTS": "73181500"}


@pytest.fixture
def served_master(monkeypatch):
    """The customer's parsed sheet, as the cache would hand it back. Patched because the real
    one is read off an uploaded workbook in the uploads dir, which no test has."""
    import app.core.material_master as mm

    monkeypatch.setattr(mm, "cached_mapping", lambda *a, **k: dict(MASTER))
    return MASTER


def _setup(db_session, lines):
    """A job whose one invoice carries `lines` - (description, part code, stored CTH) each."""
    tenant = make_tenant(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-cth@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                     kind="lookup", per_row=True, lookup_key_label="Customer Part code")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CTHSRC",
              status="extracted", created_by_id=operator.id, assigned_operator_id=operator.id)
    db_session.add(job)
    db_session.commit()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="/tmp/invoice.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    for n, (desc, part, cth) in enumerate(lines, start=1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, row_index=n,
            label_name="product_description", extracted_value=desc,
        ))
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, row_index=n,
            label_name="Customer Part code", extracted_value=part,
        ))
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id, row_index=n,
            label_name="CTH", extracted_value=cth,
        ))
    db_session.commit()
    return tenant, operator, job


def _sources(client, job):
    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    return {
        fv["row_index"]: fv.get("lookup_source")
        for fv in resp.json()["field_values"]
        if fv["label_name"] == "CTH" and fv["row_index"] is not None
    }


def test_a_code_the_part_number_produced_says_part_code(client, db_session, served_master):
    tenant, operator, job = _setup(db_session, [("SOMETHING ELSE", "PART-ONE", "87089900")])
    login(client, operator.email)
    assert _sources(client, job) == {1: "Part Code"}


def test_a_code_only_the_wording_could_have_produced_says_description(
    client, db_session, served_master,
):
    """The part code is blank on the line - common on these invoices - so the description is
    the only thing that could have matched at all, which is exactly what the operator needs
    told."""
    tenant, operator, job = _setup(db_session, [("A WIDGET OF SORTS", "", "73181500")])
    login(client, operator.email)
    assert _sources(client, job) == {1: "Description"}


def test_a_value_the_sheet_does_not_produce_claims_nothing(client, db_session, served_master):
    """The operator typed this code themselves (or it was learned from an earlier correction
    rather than read off the sheet). Re-running the lookup does not reproduce it, so no source
    is claimed - this is the guard that makes computing it at read time honest."""
    tenant, operator, job = _setup(db_session, [("A WIDGET OF SORTS", "PART-ONE", "99999999")])
    login(client, operator.email)
    assert _sources(client, job) == {1: None}


def test_each_line_is_judged_on_its_own(client, db_session, served_master):
    tenant, operator, job = _setup(db_session, [
        ("SOMETHING ELSE", "PART-ONE", "87089900"),   # part code
        ("A WIDGET OF SORTS", "", "73181500"),        # description
        ("UNKNOWN THING", "", "12345678"),            # typed by hand
    ])
    login(client, operator.email)
    assert _sources(client, job) == {1: "Part Code", 2: "Description", 3: None}


def test_an_empty_cth_claims_nothing(client, db_session, served_master):
    """The note exists to explain a value that IS there; a blank line has nothing to explain."""
    tenant, operator, job = _setup(db_session, [("A WIDGET OF SORTS", "", "")])
    login(client, operator.email)
    assert _sources(client, job) == {1: None}


def test_nothing_is_claimed_when_the_sheet_is_not_in_the_cache(client, db_session, monkeypatch):
    """cached_mapping returning None means the workbook has not been parsed yet. A request
    handler must never parse it inline (it would hang), so the note simply does not appear."""
    import app.core.material_master as mm

    monkeypatch.setattr(mm, "cached_mapping", lambda *a, **k: None)
    tenant, operator, job = _setup(db_session, [("SOMETHING ELSE", "PART-ONE", "87089900")])
    login(client, operator.email)
    assert _sources(client, job) == {1: None}
