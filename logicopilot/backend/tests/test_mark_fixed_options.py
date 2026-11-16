"""A document field can carry its own list of choices, and the job screen is told about it.

Until now only a custom field could (CustomField.fixed_options). A quantity unit read off an
invoice - one of a known set of codes - was a free text box, in the header and on every
product line alike.
"""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(db):
    tenant = make_tenant(db)
    make_user(db, role=SUPER_ADMIN, email="sa-options@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready")
    db.add(group)
    db.flush()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice", order_index=0)
    db.add(tdoc)
    db.flush()
    unit = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="unit", page_number=1,
                     x=0.1, y=0.1, width=0.1, height=0.05)
    line_unit = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="product quantity type",
                          page_number=1, x=0.2, y=0.2, width=0.1, height=0.05, is_multi_value=True)
    plain = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="invoice_number",
                      page_number=1, x=0.3, y=0.3, width=0.1, height=0.05)
    db.add_all([unit, line_unit, plain])
    db.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-OPT", status="extracted")
    db.add(job)
    db.flush()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/invoice.pdf", page_count=1, original_name="invoice.pdf")
    db.add(jd)
    db.flush()
    for mark, value, row in ((unit, "PCS", None), (line_unit, "EA", 1), (plain, "INV-1", None)):
        db.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                             template_document_id=tdoc.id, job_document_id=jd.id,
                             label_name=mark.label_name, extracted_value=value, row_index=row))
    db.commit()
    return job, unit, line_unit, plain


def _by_label(client, job):
    body = client.get(f"/api/v1/jobs/{job.id}").json()
    return {fv["label_name"]: fv for fv in body["field_values"]}


def test_a_marks_choices_are_saved_and_reach_the_job_screen(client, db_session):
    job, unit, line_unit, plain = _setup(db_session)
    login(client, "sa-options@example.com")

    for mark in (unit, line_unit):
        r = client.patch(f"/api/v1/marks/{mark.id}",
                         json={"fixed_options": ["PCS", "NOS", "KGS"], "dropdown_style": "search"})
        assert r.status_code == 200, r.text
        assert r.json()["fixed_options"] == ["PCS", "NOS", "KGS"]

    fields = _by_label(client, job)
    assert fields["unit"]["fixed_options"] == ["PCS", "NOS", "KGS"]
    assert fields["unit"]["dropdown_style"] == "search"
    # the product line too - that is where the same unit was still a free text box
    assert fields["product quantity type"]["fixed_options"] == ["PCS", "NOS", "KGS"]
    # a field nobody gave a list is untouched
    assert not fields["invoice_number"].get("fixed_options")
    assert not fields["invoice_number"].get("dropdown_style")


def test_blanks_and_repeats_are_dropped_and_an_empty_list_means_a_text_box_again(client, db_session):
    job, unit, _line, _plain = _setup(db_session)
    login(client, "sa-options@example.com")

    r = client.patch(f"/api/v1/marks/{unit.id}", json={"fixed_options": ["PCS", " ", "pcs", "RLS", "RLS", " NOS "]})
    assert r.json()["fixed_options"] == ["PCS", "RLS", "NOS"]

    r = client.patch(f"/api/v1/marks/{unit.id}", json={"fixed_options": []})
    assert r.status_code == 200
    assert r.json()["fixed_options"] is None
    assert not _by_label(client, job)["unit"].get("fixed_options")


def test_an_unknown_dropdown_style_is_refused(client, db_session):
    _job, unit, _line, _plain = _setup(db_session)
    login(client, "sa-options@example.com")

    r = client.patch(f"/api/v1/marks/{unit.id}", json={"dropdown_style": "fancy"})
    assert r.status_code == 422
    db_session.expire_all()
    assert db_session.get(FieldMark, unit.id).dropdown_style is None


def test_editing_something_else_leaves_the_choices_alone(client, db_session):
    _job, unit, _line, _plain = _setup(db_session)
    login(client, "sa-options@example.com")
    client.patch(f"/api/v1/marks/{unit.id}", json={"fixed_options": ["PCS", "NOS"]})

    r = client.patch(f"/api/v1/marks/{unit.id}", json={"value_format_hint": "one of the codes"})
    assert r.json()["fixed_options"] == ["PCS", "NOS"]
