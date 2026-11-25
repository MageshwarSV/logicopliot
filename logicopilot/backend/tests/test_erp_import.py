"""Import current setup: each ERP Excel column gets its category from the template's mapping."""
from types import SimpleNamespace as NS

from app.core.erp_import import ADDITIONAL_ID, classify_column, field_index


def mark(mid, label, doc, drawn=True, **kw):
    base = dict(id=mid, label_name=label, document_id=doc, width=.1 if drawn else 0, height=.1 if drawn else 0,
                page_number=1, x=.1, y=.1, is_target_value=False, ask_operator=False, ask_operator_required=False,
                ask_operator_hint=None, is_multi_value=False, display_label=None, fixed_options=None,
                date_picker=False, hardcoded_value=None, example_value="X", detected_anchor="A")
    base.update(kw)
    return NS(**base)


def cf(label, kind, **kw):
    base = dict(label_name=label, kind=kind, ai_prompt=None, is_target_value=False, lookup_key_label=None,
                ask_operator=False, ask_operator_required=False, ask_operator_hint=None, hardcoded_value=None,
                composite_source_labels=None, hidden_from_capture=False, display_label=None, fixed_options=None,
                date_picker=False)
    base.update(kw)
    return NS(**base)


DOCS = ["bl", "inv", "pl"]
MARKS = [mark("m1", "Invoice No", "pl", drawn=False), mark("m2", "Invoice No", "inv"),
         mark("m3", "item_quantity", "inv", is_multi_value=True),
         mark("m4", "Gross Wt", "bl"), mark("m5", "Gross Wt", "pl")]
CFS = [cf("IsUnderSec46", "hardcoded", hardcoded_value="N"),
       cf("BETypeCode", "hardcoded", ask_operator=True, ask_operator_required=True, hardcoded_value="H",
          ask_operator_hint="H for Home", fixed_options=["H", "W"]),
       cf("Origin", "ai", ai_prompt="Most lines' country"),
       cf("CTH", "lookup", lookup_key_label="Customer Part code"),
       cf("Shipment Port Code", "hardcoded", is_target_value=True, lookup_key_label="Shipment Country,Shipment City"),
       cf("Combined Description", "composite", composite_source_labels=["Customer Part code", {"fixed": "-"}, "product_description"]),
       cf("IGM No", "ai", ask_operator=True)]
IDX = field_index(MARKS, CFS)


def c(col):
    return classify_column(col, IDX, DOCS, {"bl": "Bill of lading", "inv": "Invoice", "pl": "Packing List"})


def test_document_field_goes_to_the_document_it_is_drawn_on():
    r = c({"field": "Invoice No"})
    assert r["category"] == "document" and r["source_document"] == "inv" and r["mark"].id == "m2"
    assert c({"field": "Gross Wt"})["source_document"] == "bl"                     # first drawn, template order
    assert c({"field": "Gross Wt", "from_document": "Packing List"})["source_document"] == "pl"
    assert c({"field": "item_quantity"})["line_item"] is True


def test_fixed_values_from_the_mapping_and_hardcoded_fields():
    r = c({"value": "0.00"})
    assert r["category"] == "fixed" and r["config"]["value"] == "0.00" and r["source_document"] == ADDITIONAL_ID
    r = c({"values": ["INR", "USD"]})
    assert r["config"]["value"] == "INR | USD" and "One value per row" in r["description"]
    assert c({"values": ["1.0", "[[Exchange Rate]]"]})["description"].endswith("Exchange Rate")
    assert c({"field": "IsUnderSec46"})["config"]["value"] == "N"


def test_operator_reference_ai_formula():
    r = c({"field": "BETypeCode"})
    assert r["category"] == "operator" and r["mandatory"] and r["config"] == {"input_type": "dropdown", "choices": ["H", "W"], "default": "H"}
    assert c({"field": "IGM No"})["category"] == "operator"                        # asked, no AI prompt
    assert c({"field": "Origin"})["category"] == "ai"
    assert c({"field": "CTH"})["category"] == "reference"
    assert c({"field": "Shipment Port Code"})["category"] == "reference"
    r = c({"field": "Combined Description"})
    assert r["category"] == "formula" and r["formula"] == 'Customer Part code + "-" + product_description'
    assert c({"field": "#row"})["category"] == "formula"
    assert c({"field": "container_type", "transform": "leading_digits"})["category"] is None   # field missing
    assert c({"field": "Gross Wt", "transform": "leading_digits"})["category"] == "formula"
    assert c({"field": "Supplier Name", "pick_by": {"field": "Supplier Source", "options": {"a": "shipper"}}})["category"] == "formula"


def test_blank_and_missing_are_left_unclassified():
    assert c({"field": "", "header": "AD_Code"})["category"] is None
    r = c({"field": "container_number"})
    assert r["category"] is None and "does not have" in r["description"]
    assert c(None)["category"] is None
