"""An Excel column whose field is chosen by another field's value ("pick_by").

November 5: INVOICES Supplier_Name / Supplier_Address take the invoice's Shipper OR its
Sold By / Exporter, whichever the operator ticked "Use this value" on (stored in the hidden
"Supplier Source" field). Nothing ticked means Shipper.
"""
from app.core.excel_entry import _rows_for_plan

PICK_NAME = {"field": "Supplier Source", "default": "shipper",
             "options": {"shipper": "shipper", "Sold By / Buyer Name": "Sold By / Buyer Name"}}
PICK_ADDR = {"field": "Supplier Source", "default": "shipper",
             "options": {"shipper": "Shipper Address", "Sold By / Buyer Name": "Sold By / Buyer Name Address"}}
PLAN = {"sheet": "INVOICES", "header_row": 1, "scope": "job", "columns": [
    {"column": "AE", "header": "Supplier_Name", "field": "Supplier Name", "pick_by": PICK_NAME},
    {"column": "AF", "header": "Supplier_Address", "field": "Supplier Address", "pick_by": PICK_ADDR},
    {"column": "B", "header": "Invoice_No", "field": "Invoice No"},
]}
VALUES = {
    "shipper": "Juniper Networks (US), Inc.", "Shipper Address": "1133 Innovation Way, Sunnyvale, USA",
    "Sold By / Buyer Name": "Juniper Networks International B.V.",
    "Sold By / Buyer Name Address": "Boeing Avenue, 240, Schiphol 1119 PZ, Netherlands",
    "Supplier Name": "AWB SUPPLIER", "Supplier Address": "AWB ADDRESS", "Invoice No": "5001453064",
}


def test_nothing_ticked_uses_the_shipper():
    assert _rows_for_plan(PLAN, dict(VALUES), {}) == [[
        "Juniper Networks (US), Inc.", "1133 Innovation Way, Sunnyvale, USA", "5001453064"]]


def test_shipper_ticked_uses_the_shipper():
    v = {**VALUES, "Supplier Source": "shipper"}
    assert _rows_for_plan(PLAN, v, {})[0][:2] == [
        "Juniper Networks (US), Inc.", "1133 Innovation Way, Sunnyvale, USA"]


def test_sold_by_ticked_uses_sold_by_name_and_its_address():
    v = {**VALUES, "Supplier Source": "Sold By / Buyer Name"}
    assert _rows_for_plan(PLAN, v, {})[0][:2] == [
        "Juniper Networks International B.V.", "Boeing Avenue, 240, Schiphol 1119 PZ, Netherlands"]


def test_an_unknown_choice_falls_back_to_the_default():
    v = {**VALUES, "Supplier Source": "something else"}
    assert _rows_for_plan(PLAN, v, {})[0][0] == "Juniper Networks (US), Inc."


def test_a_column_without_pick_by_is_unchanged():
    plan = {**PLAN, "columns": [{"column": "AE", "header": "Supplier_Name", "field": "Supplier Name"}]}
    assert _rows_for_plan(plan, dict(VALUES), {}) == [["AWB SUPPLIER"]]
