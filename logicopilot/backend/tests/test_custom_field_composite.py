"""CustomField.kind == "composite" - a per-row field whose value is assembled purely by
joining OTHER already-computed fields on the SAME line (marks or other custom fields, named
by label_name, in a chosen order), with a single space, skipping blanks. No AI prompt, no
reference sheet - a pure join over data the job already has.

Deliberately not a picker (see test_custom_field_pairing.py) and not a new document-reading
AI field (see test_custom_field_per_row_ai.py) - this is the general mechanism requested to
replace a hardcoded "(PART CODE) DESCRIPTION" prompt with a configurable, reusable join."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
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
    for label in ("item_material_code", "product_description"):
        db_session.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name=label,
                                 page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                                 is_multi_value=True))
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc, reference, status="extracting"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job, jd


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [(r.row_index, r.extracted_value) for r in rows]


def _empty_ocr_patches():
    return patch("app.api.v1.jobs.get_page_ocr",
                return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
        patch("app.api.v1.jobs.extract_document_fields", return_value={})


def test_run_extraction_composite_joins_pieces_in_order_skipping_blanks(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="product_description_combined",
        kind="composite", per_row=True,
        composite_source_labels=["item_material_code", "product_description"],
    )
    db_session.add(cf)
    db_session.commit()
    job, _jd = _make_job(db_session, tenant, group, tdoc, "JOB-COMPOSITE1")

    text_rows = [
        {"item_material_code": "3B04F4000-000", "product_description": "DM257 S"},
        {"item_material_code": "", "product_description": "NO CODE ROW"},
        {"item_material_code": "9Z00X", "product_description": ""},
    ]
    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "product_description_combined") == [
        (1, "3B04F4000-000 DM257 S"),
        (2, "NO CODE ROW"),
        (3, "9Z00X"),
    ]


def test_run_extraction_composite_runs_after_its_own_ingredients(db_session):
    """A composite field can name ANOTHER custom field as one of its pieces - it must be
    computed after that field has already written its own per-row value this same run, not
    before (custom_fields has no natural ordering guarantee - see the sort this relies on)."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    upstream = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                           kind="ai", per_row=True, ai_prompt="read off the document")
    db_session.add(upstream)
    composite = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Combined",
        kind="composite", per_row=True,
        composite_source_labels=["product_description", "Document CTH"],
    )
    db_session.add(composite)
    db_session.commit()
    job, _jd = _make_job(db_session, tenant, group, tdoc, "JOB-COMPOSITE2")

    text_rows = [{"item_material_code": "MC1", "product_description": "WIDGET"}]
    p1, p2 = _empty_ocr_patches()
    with p1, p2, \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.core.llm.compute_custom_field_per_row", return_value=["8483109090"]):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "Combined") == [(1, "WIDGET 8483109090")]


def test_recompute_composite_field_recomputes(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Combined",
        kind="composite", per_row=True,
        composite_source_labels=["item_material_code", "product_description"],
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-COMPOSITE3", status="extracted")
    for i, (code, desc) in enumerate([("MC1", "WIDGET 1"), ("", "WIDGET 2")], start=1):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                     label_name="item_material_code", extracted_value=code, row_index=i))
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                     label_name="product_description", extracted_value=desc, row_index=i))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-composite@example.com")
    login(client, "sa-composite@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert _row_values(db_session, job, "Combined") == [(1, "MC1 WIDGET 1"), (2, "WIDGET 2")]


def test_recompute_rejects_a_job_level_composite_field(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Combined",
                     kind="composite", per_row=False, composite_source_labels=["product_description"])
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job, _jd = _make_job(db_session, tenant, group, tdoc, "JOB-COMPOSITE4", status="extracted")

    make_user(db_session, role=SUPER_ADMIN, email="sa-composite2@example.com")
    login(client, "sa-composite2@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 400


def test_composite_field_value_is_self_filled_on_the_job(client, db_session):
    """Self-filled (like a reference-sheet lookup) so it shows on the per-document Product
    Detail card without needing "ask the operator" ticked - see jobs.py's self_fill_ids."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Combined",
        kind="composite", per_row=True, ask_operator=False,
        composite_source_labels=["item_material_code", "product_description"],
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-COMPOSITE5", status="extracted")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                 custom_field_id=cf.id, label_name="Combined",
                                 extracted_value="MC1 WIDGET 1", row_index=1))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-composite@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    fv = next(f for f in resp.json()["field_values"] if f["label_name"] == "Combined")
    assert fv["self_filled"] is True


def test_create_endpoint_honours_composite_source_labels_at_creation(client, db_session):
    """Bug found while verifying this feature live: the create route built the CustomField
    without paired_custom_field_id/picker_heading/sync_field_ids/composite_source_labels at
    all, so a brand-new composite field lost its recipe silently unless immediately
    followed by a second PATCH."""
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)

    make_user(db_session, role=SUPER_ADMIN, email="sa-composite4@example.com")
    login(client, "sa-composite4@example.com")

    resp = client.post(f"/api/v1/template-groups/{group.id}/custom-fields", json={
        "label_name": "Combined",
        "kind": "composite",
        "per_row": True,
        "composite_source_labels": ["item_material_code", "product_description"],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["composite_source_labels"] == ["item_material_code", "product_description"]


def test_edit_endpoint_can_set_and_clear_composite_source_labels(client, db_session):
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Combined",
                     kind="hardcoded", hardcoded_value="x")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-composite3@example.com")
    login(client, "sa-composite3@example.com")

    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={
        "kind": "composite",
        "per_row": True,
        "composite_source_labels": ["item_material_code", "product_description"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["composite_source_labels"] == ["item_material_code", "product_description"]

    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"composite_source_labels": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["composite_source_labels"] is None
