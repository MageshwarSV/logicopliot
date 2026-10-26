"""Combined Description Configuration - a composite field's full FORMAT (field order, fixed
text, and each piece's own separator) saved directly against a consignee
(CompositeFieldConsigneeDefault), independent of any one job, so a new job for the SAME
consignee pre-fills with, and computes using, that consignee's own saved format rather than
the template's generic fallback.

The latest save always replaces what a consignee already had - no confirmation step (a
deliberate admin action through this screen, not an incidental edit made while working a job)."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.core.system_settings import set_extraction_engine
from app.models.composite_consignee_default import CompositeFieldConsigneeDefault
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
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
    for label in ("item_material_code", "product_description", "Customer Part code"):
        db_session.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name=label,
                                 page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                                 is_multi_value=True))
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc, reference, consignee=None, status="extracted"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    if consignee is not None:
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                     label_name="consignee_full_name", extracted_value=consignee))
        db_session.commit()
    return job, jd


def _add_rows(db_session, tenant, job, jd, rows):
    for i, values in enumerate(rows, start=1):
        for label, value in values.items():
            db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                         label_name=label, extracted_value=value, row_index=i))
    db_session.commit()


def test_saving_a_format_remembers_it_for_that_consignee_only(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons1@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "label_name": "Combined Description",
        "pieces": ["Customer Part code", "product_description"],
        "separators": ["", " "],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["field_values"] is None  # no job_id given

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    default_row = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    assert default_row.source_labels == ["Customer Part code", "product_description"]
    assert default_row.separators == ["", " "]


def test_a_new_job_for_the_same_consignee_computes_with_their_saved_format(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons2@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["product_description", {"fixed": "-"}, "Customer Part code"],
        "separators": ["", " ", " "],
    })
    assert resp.status_code == 200, resp.text

    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS2", consignee="ACME CORP",
                        status="extracting")
    text_rows = [{"Customer Part code": "MC2", "product_description": "WIDGET 2"}]
    set_extraction_engine(db_session, "ocr_gpt4o_mini")
    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "x", "text": "x", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows):
        run_extraction(db_session, job)

    combined = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "Combined Description")
        .one()
    )
    assert combined.extracted_value == "WIDGET 2 - MC2"


def test_a_different_consignee_is_unaffected_and_falls_back_to_the_generic_default(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons3@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["product_description", "Customer Part code"],
        "separators": ["", " "],
    })
    assert resp.status_code == 200, resp.text

    # A DIFFERENT consignee, never configured - falls back to the generic default (set by
    # ACME's own save above) rather than an empty form.
    resp = client.get(
        f"/api/v1/template-groups/{group.id}/composite-field-config",
        params={"consignee": "OTHER BUYER LTD"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pieces"] == ["product_description", "Customer Part code"]
    assert body["separators"] == ["", " "]

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "OTHER BUYER LTD",
        "pieces": ["Customer Part code", "product_description"],
        "separators": [" ", " "],
    })
    assert resp.status_code == 200, resp.text

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    acme_default = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    # ACME's own saved format is still exactly what it was - the other consignee's save never
    # touched it.
    assert acme_default.source_labels == ["product_description", "Customer Part code"]


def test_fixed_value_pieces_are_remembered_per_consignee(client, db_session):
    # Unlike the old (pre-Configuration-screen) design, a format's own fixed text (a bracket, a
    # dash) IS part of what gets saved and reused now - see the model's own docstring.
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons4@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["Customer Part code", {"fixed": "("}, "product_description", {"fixed": ")"}],
        "separators": ["", "", " ", ""],
    })
    assert resp.status_code == 200, resp.text

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    default_row = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    assert default_row.source_labels == [
        "Customer Part code", {"fixed": "("}, "product_description", {"fixed": ")"},
    ]


def test_saving_always_overwrites_with_no_confirmation_step(client, db_session):
    # Confirmed with the user: this screen is a deliberate admin action, unlike the job
    # screen's old inline modal - the latest save always replaces the previous one outright.
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons6@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["Customer Part code", "product_description"],
        "separators": ["", " "],
    })
    assert resp.status_code == 200, resp.text

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["product_description", "Customer Part code"],
        "separators": ["", ", "],
    })
    assert resp.status_code == 200, resp.text

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    default_row = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    assert default_row.source_labels == ["product_description", "Customer Part code"]
    assert default_row.separators == ["", ", "]


def test_saving_with_a_job_id_recomputes_and_returns_that_jobs_field_values(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS7", consignee="ACME CORP")
    _add_rows(db_session, tenant, job, jd, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons7@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "pieces": ["Customer Part code", "product_description"],
        "separators": ["", " - "],
        "job_id": job.id,
    })
    assert resp.status_code == 200, resp.text
    values = resp.json()["field_values"]
    assert values is not None
    assert values[0]["value"] == "MC1 - WIDGET 1"


def test_blank_consignee_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons8@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "   ",
        "pieces": ["Customer Part code"],
    })
    assert resp.status_code == 400


def test_get_config_returns_real_sample_rows_for_the_live_preview(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS10", consignee="ACME CORP")
    _add_rows(db_session, tenant, job, jd, [
        {"Customer Part code": "MC1", "product_description": "WIDGET 1"},
        {"Customer Part code": "MC2", "product_description": "WIDGET 2"},
    ])
    # A DIFFERENT consignee's own product lines must never leak into ACME's sample rows.
    other_job, other_jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS10B", consignee="OTHER BUYER LTD")
    _add_rows(db_session, tenant, other_job, other_jd, [{"Customer Part code": "ZZ9", "product_description": "GADGET"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons10@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/composite-field-config",
                      params={"consignee": "ACME CORP"})
    assert resp.status_code == 200, resp.text
    samples = resp.json()["sample_rows"]
    assert len(samples) == 2
    part_codes = {s.get("Customer Part code") for s in samples}
    assert part_codes == {"MC1", "MC2"}
    assert not any(s.get("Customer Part code") == "ZZ9" for s in samples)


def test_known_consignees_lists_every_distinct_consignee_seen_on_this_template(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    _make_job(db_session, tenant, group, tdoc, "JOB-CONS9A", consignee="ACME CORP")
    _make_job(db_session, tenant, group, tdoc, "JOB-CONS9B", consignee="OTHER BUYER LTD")
    _make_job(db_session, tenant, group, tdoc, "JOB-CONS9C", consignee="ACME CORP")  # duplicate
    _make_job(db_session, tenant, group, tdoc, "JOB-CONS9D", consignee=None)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons9@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/known-consignees")
    assert resp.status_code == 200, resp.text
    assert resp.json() == ["ACME CORP", "OTHER BUYER LTD"]
