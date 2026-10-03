"""CompositeFieldConsigneeDefault - a composite field's piece ORDER remembered per consignee
(consignee_full_name), so a new job for the SAME consignee pre-fills with, and computes
using, that consignee's own remembered order rather than the template's generic fallback.

Deliberately never stores a fixed-value piece - see the model's own docstring - only the
field-reference pieces of whatever was last applied for that consignee."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
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


def test_applying_an_order_remembers_it_for_that_consignee_only(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS1", consignee="ACME CORP")
    _add_rows(db_session, tenant, job, jd, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons1@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", "product_description"],
    })
    assert resp.status_code == 200, resp.text

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    default_row = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    assert default_row.source_labels == ["Customer Part code", "product_description"]


def test_a_new_job_for_the_same_consignee_pre_fills_and_computes_with_their_remembered_order(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job_a, jd_a = _make_job(db_session, tenant, group, tdoc, "JOB-CONS2A", consignee="ACME CORP")
    _add_rows(db_session, tenant, job_a, jd_a, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons2@example.com")
    login(client, op.email)

    # Job A applies "description first, then part code" - the REVERSE of the usual order.
    resp = client.put(f"/api/v1/jobs/{job_a.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["product_description", "Customer Part code"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["value"] == "WIDGET 1 MC1"

    # Job B, same consignee, has NEVER had the field applied to it directly - a fresh
    # extraction should still compute it in that consignee's remembered order.
    job_b, jd_b = _make_job(db_session, tenant, group, tdoc, "JOB-CONS2B", consignee="ACME CORP",
                            status="extracting")
    text_rows = [{"Customer Part code": "MC2", "product_description": "WIDGET 2"}]
    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "x", "text": "x", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows):
        run_extraction(db_session, job_b)

    combined = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job_b.id, JobFieldValue.label_name == "Combined Description")
        .one()
    )
    assert combined.extracted_value == "WIDGET 2 MC2"

    # The GET endpoint's pre-fill agrees with what was actually used.
    resp = client.get(f"/api/v1/jobs/{job_b.id}/composite-fields")
    assert resp.json()["existing"]["composite_source_labels"] == ["product_description", "Customer Part code"]


def test_a_different_consignee_is_unaffected_and_falls_back_to_the_generic_default(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job_a, jd_a = _make_job(db_session, tenant, group, tdoc, "JOB-CONS3A", consignee="ACME CORP")
    _add_rows(db_session, tenant, job_a, jd_a, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons3@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job_a.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["product_description", "Customer Part code"],
    })
    assert resp.status_code == 200, resp.text

    # A DIFFERENT consignee, first time seeing this field at all - applies its own order,
    # independent of ACME's.
    job_b, jd_b = _make_job(db_session, tenant, group, tdoc, "JOB-CONS3B", consignee="OTHER BUYER LTD")
    _add_rows(db_session, tenant, job_b, jd_b, [{"Customer Part code": "MC9", "product_description": "GADGET"}])

    resp = client.get(f"/api/v1/jobs/{job_b.id}/composite-fields")
    assert resp.status_code == 200, resp.text
    # Falls back to the generic field default (set by job A's Apply) since OTHER BUYER LTD
    # has no remembered order of their own yet.
    assert resp.json()["existing"]["composite_source_labels"] == ["product_description", "Customer Part code"]

    resp = client.put(f"/api/v1/jobs/{job_b.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", "product_description"],
    })
    assert resp.status_code == 200, resp.text

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    acme_default = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    assert acme_default.source_labels == ["product_description", "Customer Part code"]


def test_fixed_value_pieces_are_never_remembered_per_consignee(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS4", consignee="ACME CORP")
    _add_rows(db_session, tenant, job, jd, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons4@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", {"fixed": "-"}, "product_description"],
    })
    assert resp.status_code == 200, resp.text
    # THIS job's own value still gets the fixed piece.
    assert resp.json()[0]["value"] == "MC1 - WIDGET 1"

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    default_row = (
        db_session.query(CompositeFieldConsigneeDefault)
        .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                CompositeFieldConsigneeDefault.consignee_key == "ACME CORP")
        .one()
    )
    # But the remembered order drops the fixed piece entirely.
    assert default_row.source_labels == ["Customer Part code", "product_description"]


def test_a_job_with_no_consignee_value_skips_remembering_anything(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CONS5", consignee=None)
    _add_rows(db_session, tenant, job, jd, [{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cons5@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", "product_description"],
    })
    assert resp.status_code == 200, resp.text
    assert db_session.query(CompositeFieldConsigneeDefault).count() == 0
