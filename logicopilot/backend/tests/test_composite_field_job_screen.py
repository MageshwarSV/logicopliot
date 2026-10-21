"""GET/PUT /template-groups/{group_id}/composite-field-config - the Combined Description
Configuration screen's own data, saved directly against a consignee rather than any one job.

Still a TEMPLATE-wide setting under the hood for a consignee who has never been configured
(the same CustomField row every job of the group reads - see
CustomField.composite_source_labels) - PUT only ever touches composite_source_labels/
composite_separators (and kind/per_row/label_name on first creation) - never prompts,
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


def _make_job_with_lines(db_session, tenant, group, tdoc, reference, rows, consignee="ACME CORP"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted")
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
    for i, values in enumerate(rows, start=1):
        for label, value in values.items():
            db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
                                         label_name=label, extracted_value=value, row_index=i))
    db_session.commit()
    return job


def test_get_composite_field_config_lists_available_labels_with_no_existing_format(client, db_session):
    tenant = make_tenant(db_session)
    group, _tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/composite-field-config",
                      params={"consignee": "ACME CORP"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["available_labels"]) >= {"item_material_code", "product_description", "Customer Part code"}
    assert body["pieces"] == []


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

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj6@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/composite-field-config",
                      params={"consignee": "ACME CORP"})
    assert resp.status_code == 200, resp.text
    labels = set(resp.json()["available_labels"])
    assert "RITC No." in labels
    assert "Basic_NotnSrNo" not in labels


def test_operator_can_create_and_apply_a_composite_field_from_the_configuration_screen(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-CFJ2", rows=[
        {"Customer Part code": "MC1", "product_description": "WIDGET 1"},
        {"Customer Part code": "MC2", "product_description": "WIDGET 2"},
    ])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj2@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "label_name": "Combined Description",
        "pieces": ["Customer Part code", "product_description"],
        "separators": ["", " "],
        "job_id": job.id,
    })
    assert resp.status_code == 200, resp.text
    values = {r["row_index"]: r["value"] for r in resp.json()["field_values"]}
    assert values[1] == "MC1 WIDGET 1"
    assert values[2] == "MC2 WIDGET 2"

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    assert cf.per_row is True
    assert cf.composite_source_labels == ["Customer Part code", "product_description"]

    # The template-wide setting, not job-scoped - a second, never-configured consignee sees
    # the same generic fallback.
    resp2 = client.get(f"/api/v1/template-groups/{group.id}/composite-field-config",
                       params={"consignee": "SOMEONE ELSE"})
    assert resp2.json()["pieces"] == ["Customer Part code", "product_description"]


def test_put_composite_field_rejects_empty_pieces(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj4@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP", "label_name": "Combined Description", "pieces": [],
    })
    assert resp.status_code == 400


def test_operator_cannot_reach_the_admin_only_custom_field_routes_through_this(client, db_session):
    """This door is deliberately narrow - it must not let an operator smuggle in a kind change
    or a prompt via the composite-field-config endpoint, and the real admin PATCH route must
    stay Super-Admin-only regardless."""
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


def test_fixed_value_piece_is_the_same_literal_text_on_every_line(client, db_session):
    """A piece doesn't have to be another field - {"fixed": "<text>"} is a literal typed
    straight in, joined in at that position on every line the same way."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-CFJ7", rows=[
        {"Customer Part code": "MC1", "product_description": "WIDGET 1"},
        {"Customer Part code": "MC2", "product_description": "WIDGET 2"},
    ])

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj7@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "label_name": "Combined Description",
        "pieces": ["Customer Part code", {"fixed": "-"}, "product_description"],
        "separators": ["", " ", " "],
        "job_id": job.id,
    })
    assert resp.status_code == 200, resp.text
    values = {r["row_index"]: r["value"] for r in resp.json()["field_values"]}
    assert values[1] == "MC1 - WIDGET 1"
    assert values[2] == "MC2 - WIDGET 2"

    cf = db_session.query(CustomField).filter(CustomField.group_id == group.id, CustomField.kind == "composite").one()
    assert cf.composite_source_labels == ["Customer Part code", {"fixed": "-"}, "product_description"]


def test_fixed_value_piece_cannot_be_blank(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj8@example.com")
    login(client, op.email)

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "label_name": "Combined Description",
        "pieces": ["product_description", {"fixed": "  "}],
    })
    assert resp.status_code == 400


def test_available_labels_excludes_the_composite_fields_own_label(client, db_session):
    """A composite field can't usefully combine itself - offering its own label as a
    choosable piece would let an operator build a circular reference."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Combined Description",
                     kind="composite", per_row=True, composite_source_labels=["product_description"])
    db_session.add(cf)
    db_session.commit()

    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cfj9@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/composite-field-config",
                      params={"consignee": "ACME CORP"})
    assert resp.status_code == 200, resp.text
    assert "Combined Description" not in resp.json()["available_labels"]

    resp = client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ACME CORP",
        "label_name": "Combined Description",
        "pieces": ["Combined Description"],
    })
    assert resp.status_code == 400
