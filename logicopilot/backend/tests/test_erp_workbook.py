"""Onboarding Step 1: an ERP workbook's sheets and columns come straight from the file."""
from datetime import datetime

import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.core.erp_workbook import (  # noqa: E402
    column_letter, detect_header_row, read_workbook, suggest_label, suggest_type,
)


def _book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "INVOICES"
    ws.append(["InvSrNo", "Invoice_No", "Invoice_Date", "Product_Value"])
    ws.append([1, "EX-014492", datetime(2026, 9, 24), 6612.48])
    ws.append([2, "EX-014493", datetime(2026, 9, 25), 100.5])
    items = wb.create_sheet("ITEMS")
    items.append(["Import template - do not edit"])          # a title above the headings
    items.append([])
    items.append(["ItemSrNo", None, "Qty", "UOM", None, None])  # blank middle kept, trailing dropped
    items.append([1, None, 384, "NOS"])
    wb.create_sheet("EMPTY")
    path = tmp_path / "erp.xlsx"
    wb.save(path)
    return path


def test_reads_every_sheet_and_column_from_the_file(tmp_path):
    sheets = read_workbook(_book(tmp_path))
    assert [s["name"] for s in sheets] == ["INVOICES", "ITEMS", "EMPTY"]
    inv = sheets[0]
    assert inv["header_row"] == 1
    assert [c["name"] for c in inv["columns"]] == ["InvSrNo", "Invoice_No", "Invoice_Date", "Product_Value"]
    assert [c["key"] for c in inv["columns"]] == ["A", "B", "C", "D"]
    types = {c["name"]: c["suggested_type"] for c in inv["columns"]}
    assert types == {"InvSrNo": "number", "Invoice_No": "text", "Invoice_Date": "date", "Product_Value": "decimal"}
    assert inv["columns"][1]["samples"] == ["EX-014492", "EX-014493"]


def test_heading_row_below_a_title_and_blank_columns(tmp_path):
    items = read_workbook(_book(tmp_path))[1]
    assert items["header_row"] == 3
    assert [(c["key"], c["name"]) for c in items["columns"]] == [("A", "ItemSrNo"), ("B", ""), ("C", "Qty"), ("D", "UOM")]


def test_admin_can_correct_the_heading_row(tmp_path):
    items = read_workbook(_book(tmp_path), header_rows={"ITEMS": 4})[1]
    assert items["header_row"] == 4
    assert items["columns"][0]["name"] == "1"


def test_suggestions():
    assert suggest_label("Invoice_No") == "Invoice No"
    assert suggest_label("InvSrNo") == "Inv Sr No"
    assert suggest_type([]) == "text"
    assert suggest_type([1, 2.0]) == "number"
    assert suggest_type([1, "x"]) == "text"
    assert column_letter(1) == "A" and column_letter(27) == "AA"
    assert detect_header_row([["Title"], [], ["a", "b", "c"]]) == 3
