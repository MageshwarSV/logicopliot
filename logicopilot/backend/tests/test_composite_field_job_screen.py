"""GET/PUT /jobs/{job_id}/composite-fields - lets anyone reviewing a job (not just a Super
Admin in the template wizard) pick the piece order for the template's "Combined Description"
-style composite field, from the job screen's own Product Detail card.

Still a TEMPLATE-wide setting under the hood (the same CustomField row every job of the group
reads - see CustomField.composite_source_labels) - these routes are a narrower, operator-safe
front door onto that one mechanism, not a second one. PUT only ever touches
composite_source_labels (and kind/per_row/label_name on first creation) - never prompts,
hardcoded values, or any other admin-only config."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, SUPER_ADMIN
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


def _make_job_with_lines(db_session, tenant, group, tdoc, reference, rows):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    for i, values in enumerate(rows, start=1):
        for label, value in values.items():
            db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                         label_name=label, extracted_value=value, row_index=i))
    db_session.commit()
    return job


def test_get_composite_fields_lists_available_labels_and_no_existing_field(client, db_session):
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    job = _make_job_with_lines(db_session, tenant, group, _tdoc, "JOB-CFJ1",
                               rows=[{"product_description": "WIDGET"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/composite-fields")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["available_labels"]) >= {"item_material_code", "product_description", "Customer Part code"}
    assert body["existing"] is None


def test_available_labels_excludes_per_row_fields_that_never_show_on_product_detail(client, db_session):
    """A plain ask_operator per-row field with no pairing (a duty notification number, say)
    only ever shows on Additional Details, never on Product Detail - so it must not be
    offerable as a "piece" to combine into a field that DOES show there. Only a self-filled
    (lookup/composite) or paired per-row field belongs in available_labels."""
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    duty_field = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Basic_NotnSrNo",
                             kind="hardcoded", per_row=True, ask_operator=True)
    lookup_field = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="RITC No.",
                               kind="lookup", per_row=True)
    db_session.add_all([duty_field, lookup_field])
    db_session.commit()
    job = _make_job_with_lines(db_session, tenant, group, _tdoc, "JOB-CFJ6",
                               rows=[{"product_description": "WIDGET"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj6@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/composite-fields")
    assert resp.status_code == 200, resp.text
    labels = set(resp.json()["available_labels"])
    assert "RITC No." in labels
    assert "Basic_NotnSrNo" not in labels


def test_operator_can_create_and_apply_a_composite_field_from_the_job_screen(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-CFJ2", rows=[
        {"Customer Part code": "MC1", "product_description": "WIDGET 1"},
        {"Customer Part code": "MC2", "product_description": "WIDGET 2"},
    ])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj2@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", "product_description"],
    })
    assert resp.status_code == 200, resp.text
    values = {(r["row_index"]): r["value"] for r in resp.json()}
    assert values[1] == "MC1 WIDGET 1"
    assert values[2] == "MC2 WIDGET 2"

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    assert cf.per_row is True
    assert cf.composite_source_labels == ["Customer Part code", "product_description"]

    # The template-wide setting, not job-scoped - a second job of the same template sees
    # the field defined (it just hasn't been recomputed onto it yet).
    resp2 = client.get(f"/api/v1/jobs/{job.id}/composite-fields")
    assert resp2.json()["existing"]["composite_source_labels"] == ["Customer Part code", "product_description"]


def test_reordering_from_the_job_screen_changes_the_template_wide_rule(client, db_session):
    """Applying a new order from ANY job updates the SAME CustomField - this is the
    template-wide-rule behaviour the user explicitly chose over a per-job override."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Combined Description",
                     kind="composite", per_row=True,
                     composite_source_labels=["product_description", "Customer Part code"])
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    job_a = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-CFJ3A",
                                 rows=[{"Customer Part code": "MC1", "product_description": "WIDGET 1"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj3@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job_a.id}/composite-fields", json={
        "label_name": "Combined Description",
        "source_labels": ["Customer Part code", "product_description"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["value"] == "MC1 WIDGET 1"

    db_session.refresh(cf)
    assert cf.composite_source_labels == ["Customer Part code", "product_description"]


def test_put_composite_field_rejects_empty_pieces(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-CFJ4",
                               rows=[{"product_description": "WIDGET"}])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj4@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/jobs/{job.id}/composite-fields", json={
        "label_name": "Combined Description", "source_labels": [],
    })
    assert resp.status_code == 400


def test_operator_cannot_reach_the_admin_only_custom_field_routes_through_this(client, db_session):
    """This new door is deliberately narrow - it must not let an operator smuggle in a kind
    change or a prompt via the composite endpoint, and the real admin PATCH route must stay
    Super-Admin-only regardless."""
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Some Admin Field",
                     kind="hardcoded", hardcoded_value="x")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj5@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"kind": "ai", "ai_prompt": "hacked"})
    assert resp.status_code == 403

    make_user(db_session, role=SUPER_ADMIN, email="sa-cfj5@example.com")
    login(client, "sa-cfj5@example.com")
    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"kind": "ai", "ai_prompt": "hacked"})
    assert resp.status_code == 200
