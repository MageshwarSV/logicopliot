"""ERP UI fields: job fields the ERP script picks on screen that are not ERP Excel columns."""
from types import SimpleNamespace as NS

import pytest

from app.core.erp_field_config import ConfigError, clean_config, is_configured
from app.core.erp_import import classify_column, field_index
from app.core.erp_ui_fields import excel_labels, reference_config, script_labels, with_parents


def cf(label, kind="hardcoded", **kw):
    base = dict(label_name=label, kind=kind, ai_prompt=None, is_target_value=False, lookup_key_label=None,
                ask_operator=False, ask_operator_required=False, ask_operator_hint=None, hardcoded_value=None,
                composite_source_labels=None, hidden_from_capture=False, display_label=None, fixed_options=None,
                date_picker=False)
    base.update(kw)
    return NS(**base)


def test_script_labels_walks_every_step_kind_once_in_order():
    steps = [
        {"action": "fill", "field_label": "Erp Entry Name"},
        {"action": "select", "value": "[[branch_name]]", "value_fields": ["Destination", "branch_name"]},
        {"action": "if_else", "then_steps": [{"action": "fill", "prompt": "Use [[ERP Importer Address / Branch Code]]"}],
         "else_steps": [{"action": "fill", "field_label": "Erp Entry Name"}]},
        {"action": "click", "selector": "#save"},
    ]
    assert script_labels(steps, "Destination") == [
        "Erp Entry Name", "branch_name", "Destination", "ERP Importer Address / Branch Code"]


def test_excel_labels_include_pick_by_and_fallbacks():
    cfg = {"sheets": [{"sheet": "GENERAL", "columns": [
        {"header": "A", "field": "Consignee"},
        {"header": "B", "pick_by": {"field": "Mode", "options": {"FCL": "Container No"}}},
        {"header": "C", "field": "Gross Wt", "fallback_fields": ["Net Wt"]},
        {"header": "D", "value": "N"},
    ]}]}
    assert excel_labels(cfg) == {"Consignee", "Mode", "Container No", "Gross Wt", "Net Wt"}


def test_branch_brings_its_name_first():
    known = {"ERP Consignee Name", "ERP Importer Address / Branch Code", "Erp Entry Name", "branch_name"}
    assert with_parents(["ERP Importer Address / Branch Code", "branch_name", "Erp Entry Name"], known) == [
        "ERP Consignee Name", "ERP Importer Address / Branch Code", "Erp Entry Name", "branch_name"]
    # The parent is only added when the template has it.
    assert with_parents(["branch_name"], {"branch_name"}) == ["branch_name"]


def test_quotation_and_importer_fields_classify_as_master_references():
    cfs = [cf("Erp Entry Name", is_target_value=True, lookup_key_label="Quotation Name", ask_operator=True,
              ask_operator_required=True),
           cf("branch_name", is_target_value=True, ask_operator=True),
           cf("ERP Importer Address / Branch Code", is_target_value=True, ask_operator=True)]
    idx = field_index([], cfs)
    name = classify_column({"field": "Erp Entry Name"}, idx, [])
    assert name["category"] == "reference" and name["display_label"] == "ERP Quotation Name"
    assert name["config"]["source"] == "quotation_master" and name["config"]["level"] == "name"
    branch = classify_column({"field": "branch_name"}, idx, [])
    assert branch["config"] == {"source": "quotation_master", "level": "branch", "depends_on": "Erp Entry Name",
                                "fills": "quotation_address", "key_field": "", "pick": "operator"}
    assert "Quotation Master (branch)" in branch["erp_master_ref"]
    imp = classify_column({"field": "ERP Importer Address / Branch Code"}, idx, [])
    assert imp["display_label"] == "ERP Importer Branch" and imp["config"]["source"] == "importer_master"
    # Every config the import makes is one Step 4 accepts as it is.
    for res in (name, branch, imp):
        assert clean_config("reference", res["config"]) == res["config"]


def test_own_table_reference_keeps_its_key_field():
    c = reference_config("Port Code", cf("Port Code", lookup_key_label="Port Name"))
    assert c["source"] == "own_table" and c["key_field"] == "Port Name" and c["pick"] == "auto"


def test_reference_config_is_checked():
    with pytest.raises(ConfigError):
        clean_config("reference", {"source": "somewhere"})
    with pytest.raises(ConfigError):
        clean_config("reference", {"source": "importer_master"})          # which level?
    c = clean_config("reference", {"source": "own_table", "level": "branch", "key_field": "Port Name"})
    assert c["level"] == "" and c["pick"] == "operator"
    assert is_configured("reference", c, None)
    assert not is_configured("reference", None, None)
