"""Screen Designer preview from onboarding data, before a template has a job."""
from types import SimpleNamespace as NS

from app.core.erp_design_preview import preview_values
from app.core.erp_ui_fields import ERP_UI_SHEET


def f(fid, sheet, name, doc="inv", samples=(), category="document", default=None, display=None):
    return NS(id=fid, sheet_name=sheet, excel_name=name, source_document=doc, samples=list(samples),
              category=category, default_value=default, display_label=display)


MARKS = [NS(id="m1", label_name="invoice number", display_label=None),
         NS(id="m2", label_name="total_amount", display_label=None)]
CFS = [NS(id="c1", label_name="Erp Entry Name", display_label=None),
       NS(id="c2", label_name="IsUnderSec46", display_label=None)]
PLANS = [{"sheet": "INVOICES", "columns": [{"header": "Inv_No", "field": "invoice number"},
                                           {"header": "Invoice_Value", "field": "total_amount"}]}]


def test_box_reading_beats_excel_value_and_mapping_links_by_heading():
    fields = [f("e1", "INVOICES", "Inv_No", samples=["EX22000286"]),
              f("e2", "INVOICES", "Invoice_Value", samples=["1448.10"])]
    boxes = {"e1": NS(doc_id="inv", detected_value="EX-22000286 ")}
    out = preview_values(fields, boxes, PLANS, MARKS, CFS, "inv")
    assert out["m:m1"] == {"value": "EX-22000286", "from": "sample"}
    assert out["m:m2"] == {"value": "1448.10", "from": "excel"}


def test_erp_ui_field_by_its_own_name_and_fixed_by_name_match():
    fields = [f("e3", ERP_UI_SHEET, "Erp Entry Name", doc="additional-details", category="reference"),
              f("e4", "GENERAL", "Is_Under_Sec46", doc="additional-details", category="fixed", default="N")]
    out = preview_values(fields, {}, PLANS, MARKS, CFS, "inv")
    assert "c:c1" not in out                     # no value anywhere yet - left empty
    assert out["c:c2"] == {"value": "N", "from": "fixed"}


def test_a_box_on_another_document_is_not_used():
    fields = [f("e1", "INVOICES", "Inv_No", doc="pl", samples=["X1"])]
    out = preview_values(fields, {"e1": NS(doc_id="pl", detected_value="FROM PL")}, PLANS, MARKS, CFS, "inv")
    assert out["m:m1"] == {"value": "X1", "from": "excel"}
