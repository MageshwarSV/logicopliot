"""_sample_for_label: what gets typed live while a Super Admin is RECORDING an ERP script by
dragging a field onto an input - never what a real job replay uses (that always goes through
field_label, resolved from the job's own data - see play_steps).

An AI-computed target-value field (Quotation Value, keyed on the forwarder's name) used to fall
straight through to "no sample", which made the recorder type the field's own LABEL into the
ERP box instead - not a valid quotation number on the live ERP, so the box never validated and
recording could not continue past it. Its own reference table already holds real, previously
learned answers by the time a Super Admin re-records a flow, so this reuses the first one found
instead of the label."""
from app.api.v1.erp_scripts import _sample_for_label
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue
from app.models.erp_script import ErpScript
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_script(db_session, tenant, group):
    script = ErpScript(tenant_id=tenant.id, name="SOFTLINK(EXCEL)", url="https://erp.example",
                       template_ids=[group.id])
    db_session.add(script)
    db_session.commit()
    db_session.refresh(script)
    return script


def test_hardcoded_custom_field_uses_its_own_value(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    script = _make_script(db_session, tenant, group)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AD_Code",
                               kind="hardcoded", hardcoded_value="6430003"))
    db_session.commit()

    value, source = _sample_for_label(db_session, script, "AD_Code")
    assert value == "6430003"
    assert "hardcoded" in source


def test_target_value_ai_field_uses_a_learned_reference_value(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    script = _make_script(db_session, tenant, group)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Quotation Value",
                     kind="ai", ai_prompt="...", is_target_value=True, fuzzy_match=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add(CustomFieldReferenceValue(
        custom_field_id=cf.id, match_value_1="KUEHNENAGEL", match_value_2="", match_value_3="",
        match_value_4="", resolved_value="RFQ/0003/23-24",
    ))
    db_session.commit()

    value, source = _sample_for_label(db_session, script, "Quotation Value")
    assert value == "RFQ/0003/23-24"
    assert "learned" in source


def test_target_value_ai_field_prefers_the_most_recently_learned_row(db_session):
    """Two forwarders, two learned answers - the sample should be the NEWER one (Expeditors,
    added after Kuehne+Nagel), not whichever the table happens to return first."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    script = _make_script(db_session, tenant, group)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Quotation Value",
                     kind="ai", ai_prompt="...", is_target_value=True, fuzzy_match=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add(CustomFieldReferenceValue(
        custom_field_id=cf.id, match_value_1="KUEHNENAGEL", match_value_2="", match_value_3="",
        match_value_4="", resolved_value="RFQ/0003/23-24",
    ))
    db_session.commit()
    db_session.add(CustomFieldReferenceValue(
        custom_field_id=cf.id, match_value_1="EXPEDITORSHONGKONGLIMITED", match_value_2="",
        match_value_3="", match_value_4="", resolved_value="RFQ/0003/24-25",
    ))
    db_session.commit()

    value, source = _sample_for_label(db_session, script, "Quotation Value")
    assert value == "RFQ/0003/24-25"
    assert "learned" in source


def test_target_value_ai_field_with_nothing_learned_yet_has_no_sample(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    script = _make_script(db_session, tenant, group)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Quotation Value",
                               kind="ai", ai_prompt="...", is_target_value=True))
    db_session.commit()

    value, source = _sample_for_label(db_session, script, "Quotation Value")
    assert value == ""
    assert "AI rule" in source


def test_plain_ai_field_still_has_no_sample(db_session):
    """Unchanged behaviour: a field with no target-value reference table at all (a free-form
    AI-computed field, e.g. an invoice remark) has nothing sensible to type - it is decided per
    job from that job's own documents, same as before this fix."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    script = _make_script(db_session, tenant, group)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Remarks",
                               kind="ai", ai_prompt="..."))
    db_session.commit()

    value, source = _sample_for_label(db_session, script, "Remarks")
    assert value == ""
    assert "AI rule" in source
