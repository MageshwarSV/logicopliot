"""POST /template-groups/{group_id}/duplicate - deep-copies a whole template set. Had zero
test coverage before this file. Covers both the pre-existing same-tenant behavior (regression
protection for code this change modified) and the new cross-tenant behavior: duplicating an
already-trained template directly into a brand-new tenant as a fully independent copy."""
from app.models.cross_doc_link import CrossDocLink
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from app.models.user_template import UserTemplateAssignment
from tests.conftest import login, make_tenant, make_user


def _build_trained_group(db_session, tenant, *, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", mode=mode, status="approved",
                          ruling_prompt="some ruling", entry_mode="fields",
                          excel_config={"sheets": ["INVOICES"]},
                          pull_email="docs@customer.com")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", order_index=0, is_required=True)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    mark = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name="invoice_number",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                     extraction_prompt="the invoice number", is_multi_value=True,
                     standalone_multi_value=True, standalone_group_heading="Container",
                     is_target_value=True, fuzzy_match=True)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    hardcoded = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="GST Number",
                            kind="hardcoded", hardcoded_value="33AAACY1234D1Z1")
    lookup = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH", kind="lookup",
                         lookup_key_label="material_code",
                         lookup_match_columns=["material"], lookup_return_column="cth",
                         per_row=True, is_target_value=True, fuzzy_match=True,
                         example_value="8501")
    db_session.add_all([hardcoded, lookup])
    db_session.commit()
    db_session.refresh(hardcoded)
    db_session.refresh(lookup)

    # A picker pair: "dump" is written INTO by the operator's pick, paired with "lookup", and
    # also keeps "synced" in step with it.
    synced = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="RITC No.",
                         kind="lookup", lookup_key_label="material_code")
    db_session.add(synced)
    db_session.commit()
    db_session.refresh(synced)
    dump = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                       kind="hardcoded", paired_custom_field_id=lookup.id,
                       picker_heading="CTH - pick which one is right",
                       sync_field_ids=[synced.id])
    db_session.add(dump)
    db_session.commit()
    db_session.refresh(dump)

    composite = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Combined Desc",
                            kind="composite", composite_source_labels=["invoice_number",
                                                                       {"fixed": " - "}])
    db_session.add(composite)
    db_session.commit()
    db_session.refresh(composite)

    mark_link = CrossDocLink(tenant_id=tenant.id, group_id=group.id, source_mark_id=mark.id,
                             target_mark_id=mark.id, condition="must_equal")
    cf_link = CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                           source_custom_field_id=lookup.id, target_mark_id=mark.id,
                           condition="must_equal")
    db_session.add_all([mark_link, cf_link])
    db_session.commit()

    return group, doc, mark, {"hardcoded": hardcoded, "lookup": lookup, "synced": synced,
                              "dump": dump, "composite": composite}


def _login_super_admin(client, db_session, email="dup-sa@example.com"):
    make_user(db_session, role=SUPER_ADMIN, email=email)
    login(client, email)


def test_same_tenant_duplicate_lands_in_the_same_tenant(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, *_ = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_id = resp.json()["id"]
    copy = db_session.get(TemplateGroup, copy_id)
    assert copy.tenant_id == tenant.id
    assert copy.pull_email is None  # never copied, same-tenant or not


def test_same_tenant_duplicate_still_copies_hardcoded_value_verbatim(client, db_session):
    """The one behavioral difference between same-tenant and cross-tenant - proven here so a
    future change can't quietly blank this out for the same-tenant case too."""
    tenant = make_tenant(db_session, name="4S Logistics")
    group, doc, mark, fields = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_cf = (db_session.query(CustomField)
              .filter(CustomField.group_id == resp.json()["id"], CustomField.label_name == "GST Number")
              .one())
    assert copy_cf.hardcoded_value == "33AAACY1234D1Z1"


def test_same_tenant_name_clash_is_still_scoped_to_that_tenant(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, *_ = _build_trained_group(db_session, tenant)
    other = TemplateGroup(tenant_id=tenant.id, name="Already Taken", mode="Sea Import", status="draft")
    db_session.add(other)
    db_session.commit()
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Already Taken"})
    assert resp.status_code == 409


def test_cross_tenant_duplicate_lands_in_the_destination_tenant(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="New Customer Co")
    group, doc, mark, fields = _build_trained_group(db_session, source_tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                       json={"name": "Sea Import", "destination_tenant_id": dest_tenant.id})
    assert resp.status_code == 201, resp.text
    copy = db_session.get(TemplateGroup, resp.json()["id"])
    assert copy.tenant_id == dest_tenant.id
    assert copy.tenant_id != source_tenant.id

    copy_doc = db_session.query(TemplateDocument).filter(TemplateDocument.group_id == copy.id).one()
    assert copy_doc.tenant_id == dest_tenant.id
    copy_mark = db_session.query(FieldMark).filter(FieldMark.document_id == copy_doc.id).one()
    assert copy_mark.tenant_id == dest_tenant.id


def test_cross_tenant_duplicate_blanks_hardcoded_value(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="New Customer Co")
    group, doc, mark, fields = _build_trained_group(db_session, source_tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                       json={"name": "Sea Import", "destination_tenant_id": dest_tenant.id})
    assert resp.status_code == 201, resp.text
    copy_cf = (db_session.query(CustomField)
              .filter(CustomField.group_id == resp.json()["id"], CustomField.label_name == "GST Number")
              .one())
    assert copy_cf.hardcoded_value is None


def test_cross_tenant_name_clash_check_is_scoped_to_the_destination_only(client, db_session):
    """The SAME name already existing in the SOURCE tenant must never block a cross-tenant
    copy - only a clash in the destination tenant should."""
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="New Customer Co")
    group, *_ = _build_trained_group(db_session, source_tenant)  # named "Sea Import"
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                       json={"name": "Sea Import", "destination_tenant_id": dest_tenant.id})
    assert resp.status_code == 201, resp.text

    # Now a real clash: the destination tenant already has a group with this name.
    resp2 = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                        json={"name": "Sea Import", "destination_tenant_id": dest_tenant.id})
    assert resp2.status_code == 409


def test_cross_tenant_duplicate_refused_when_destination_not_licensed_for_the_mode(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="New Customer Co", allowed_modes=["Air Import"])
    group, *_ = _build_trained_group(db_session, source_tenant, mode="Sea Import")
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                       json={"name": "Sea Import", "destination_tenant_id": dest_tenant.id})
    assert resp.status_code == 400
    assert "Sea Import" in resp.json()["detail"]


def test_cross_tenant_duplicate_refused_for_an_unknown_destination_tenant(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    group, *_ = _build_trained_group(db_session, source_tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate",
                       json={"name": "Sea Import", "destination_tenant_id": "does-not-exist"})
    assert resp.status_code == 404


def test_all_field_mark_columns_survive_a_duplicate(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, doc, mark, fields = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_doc = db_session.query(TemplateDocument).filter(TemplateDocument.group_id == resp.json()["id"]).one()
    copy_mark = db_session.query(FieldMark).filter(FieldMark.document_id == copy_doc.id).one()
    assert copy_mark.standalone_multi_value is True
    assert copy_mark.standalone_group_heading == "Container"
    assert copy_mark.is_target_value is True
    assert copy_mark.fuzzy_match is True


def test_all_custom_field_columns_survive_a_duplicate(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, doc, mark, fields = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_group_id = resp.json()["id"]
    copy_lookup = (db_session.query(CustomField)
                  .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "CTH")
                  .one())
    assert copy_lookup.lookup_key_label == "material_code"
    assert copy_lookup.lookup_match_columns == ["material"]
    assert copy_lookup.lookup_return_column == "cth"
    assert copy_lookup.per_row is True
    assert copy_lookup.is_target_value is True
    assert copy_lookup.fuzzy_match is True
    assert copy_lookup.example_value == "8501"

    copy_composite = (db_session.query(CustomField)
                      .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "Combined Desc")
                      .one())
    assert copy_composite.composite_source_labels == ["invoice_number", {"fixed": " - "}]


def test_paired_custom_field_id_and_sync_field_ids_are_remapped_not_dropped(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, doc, mark, fields = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_group_id = resp.json()["id"]

    copy_dump = (db_session.query(CustomField)
                .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "Dump CTH Number")
                .one())
    copy_lookup = (db_session.query(CustomField)
                  .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "CTH")
                  .one())
    copy_synced = (db_session.query(CustomField)
                  .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "RITC No.")
                  .one())

    assert copy_dump.picker_heading == "CTH - pick which one is right"
    # Must point at the NEW copy's own lookup field, never the original's id.
    assert copy_dump.paired_custom_field_id == copy_lookup.id
    assert copy_dump.paired_custom_field_id != fields["lookup"].id
    assert copy_dump.sync_field_ids == [copy_synced.id]


def test_cross_doc_link_sourced_from_a_custom_field_survives_a_duplicate(client, db_session):
    """Previously silently dropped - only mark-sourced links survived."""
    tenant = make_tenant(db_session, name="4S Logistics")
    group, doc, mark, fields = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.post(f"/api/v1/template-groups/{group.id}/duplicate", json={"name": "Sea Import (v2)"})
    assert resp.status_code == 201, resp.text
    copy_group_id = resp.json()["id"]

    copy_lookup = (db_session.query(CustomField)
                  .filter(CustomField.group_id == copy_group_id, CustomField.label_name == "CTH")
                  .one())
    copy_links = db_session.query(CrossDocLink).filter(CrossDocLink.group_id == copy_group_id).all()
    cf_sourced = [l for l in copy_links if l.source_custom_field_id is not None]
    assert len(cf_sourced) == 1
    assert cf_sourced[0].source_custom_field_id == copy_lookup.id

    mark_sourced = [l for l in copy_links if l.source_mark_id is not None]
    assert len(mark_sourced) == 1


# ---------------------------------------------------------------------------
# PATCH /template-groups/{id}/tenant - reassign an EXISTING group's tenant
# ---------------------------------------------------------------------------

def test_reassigns_tenant_and_cascades_every_child_row(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari")
    group, doc, mark, fields = _build_trained_group(db_session, source_tenant)
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_id"] == dest_tenant.id

    db_session.refresh(group)
    assert group.tenant_id == dest_tenant.id
    db_session.refresh(doc)
    assert doc.tenant_id == dest_tenant.id
    db_session.refresh(mark)
    assert mark.tenant_id == dest_tenant.id
    for cf in fields.values():
        db_session.refresh(cf)
        assert cf.tenant_id == dest_tenant.id


def test_user_template_assignment_is_deleted_not_moved(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari")
    group, *_ = _build_trained_group(db_session, source_tenant)
    operator = make_user(db_session, role="operator", tenant=source_tenant, email="op-move@example.com")
    db_session.add(UserTemplateAssignment(tenant_id=source_tenant.id, user_id=operator.id, group_id=group.id))
    db_session.commit()
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 200, resp.text
    assert db_session.query(UserTemplateAssignment).filter(
        UserTemplateAssignment.group_id == group.id).count() == 0


def test_refused_when_the_group_already_has_a_job_no_partial_mutation(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari")
    group, doc, mark, fields = _build_trained_group(db_session, source_tenant)
    db_session.add(Job(tenant_id=source_tenant.id, group_id=group.id, reference="JOB-EXISTS", status="extracted"))
    db_session.commit()
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 409
    assert "duplicate" in resp.json()["detail"].lower()

    # No partial mutation - everything still shows the ORIGINAL tenant.
    db_session.refresh(group)
    assert group.tenant_id == source_tenant.id
    db_session.refresh(doc)
    assert doc.tenant_id == source_tenant.id
    db_session.refresh(mark)
    assert mark.tenant_id == source_tenant.id


def test_name_clash_in_the_destination_tenant_is_refused(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari")
    group, *_ = _build_trained_group(db_session, source_tenant)  # named "Sea Import"
    existing = TemplateGroup(tenant_id=dest_tenant.id, name="Sea Import", mode="Sea Import", status="draft")
    db_session.add(existing)
    db_session.commit()
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 409


def test_refused_when_destination_not_licensed_for_the_mode(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari", allowed_modes=["Air Import"])
    group, *_ = _build_trained_group(db_session, source_tenant, mode="Sea Import")
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 400


def test_refused_for_an_unknown_destination_tenant(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    group, *_ = _build_trained_group(db_session, source_tenant)
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": "does-not-exist"})
    assert resp.status_code == 404


def test_tenant_admin_is_refused_super_admin_only(client, db_session):
    source_tenant = make_tenant(db_session, name="4S Logistics")
    dest_tenant = make_tenant(db_session, name="Bhuvaneswari")
    group, *_ = _build_trained_group(db_session, source_tenant)
    admin = make_user(db_session, role=TENANT_ADMIN, tenant=source_tenant, email="ta-move@example.com")
    login(client, admin.email)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": dest_tenant.id})
    assert resp.status_code == 403


def test_same_tenant_is_a_harmless_no_op(client, db_session):
    tenant = make_tenant(db_session, name="4S Logistics")
    group, *_ = _build_trained_group(db_session, tenant)
    _login_super_admin(client, db_session)

    resp = client.patch(f"/api/v1/template-groups/{group.id}/tenant",
                        json={"tenant_id": tenant.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_id"] == tenant.id
