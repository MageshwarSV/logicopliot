"""excel_text: an Excel sheet read straight from its cells - exact values, each with its cell
reference - so it can be classified and extracted without converting it to a PDF. Offline: no
AI, no OCR."""
import io
from datetime import datetime

import openpyxl
import pytest

from app.core import excel_text as et
from app.core.excel_text import (WorkbookLocked, WorkbookUnreadable, find_cell, read_workbook,
                                 sheet_chunks, sheet_text)


def _xlsx(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _invoice(wb):
    ws = wb.active
    ws.title = "Detail"
    ws["A1"], ws["B1"] = "Invoice No", "MES2620601085"
    ws["A2"], ws["B2"] = "Invoice Date", datetime(2026, 11, 11)
    ws.append([])
    ws.append(["Part No", "Description", "Qty", "Amount"])
    ws.append(["K2839-01", "Capacitor  0.1uF\nX7R", 1000, 125.5])
    ws.append(["K2839-02", "Resistor", 2000.0, 80])


def test_cells_come_out_exact_with_their_references():
    wb = read_workbook(_xlsx(_invoice), "inv.xlsx")
    assert [s.name for s in wb.sheets] == ["Detail"]
    text = sheet_text(wb.sheets[0])
    assert "=== SHEET: Detail ===" in text
    assert "R1 | A: Invoice No | B: MES2620601085" in text
    assert "R2 | A: Invoice Date | B: 2026-11-11" in text                 # a date, not 46337
    assert "R5 | A: K2839-01 | B: Capacitor 0.1uF X7R | C: 1000 | D: 125.5" in text
    assert "C: 2000 | D: 80" in text                                       # 2000.0 -> 2000
    assert "R3" not in text                                                # empty row dropped


def test_find_cell_points_back_to_the_cell():
    sheet = read_workbook(_xlsx(_invoice), "inv.xlsx").sheets[0]
    assert find_cell(sheet, "MES2620601085") == "Detail!B1"
    assert find_cell(sheet, "125.5") == "Detail!D5"
    assert find_cell(sheet, "nothing like this") is None


def test_hidden_sheets_are_skipped_and_named():
    def build(wb):
        wb.active.title = "12.11.26"
        wb.active["A1"] = "current"
        old = wb.create_sheet("08.10.26")
        old["A1"] = "old"
        old.sheet_state = "hidden"
    wb = read_workbook(_xlsx(build), "rohm.xlsx")
    assert [s.name for s in wb.sheets] == ["12.11.26"] and wb.hidden == ["08.10.26"]


def test_merged_cells_are_filled_so_every_row_has_its_value():
    def build(wb):
        ws = wb.active
        ws["A1"] = "Carton 1"
        ws.merge_cells("A1:A3")
        ws["B1"], ws["B2"], ws["B3"] = "item a", "item b", "item c"
    rows = read_workbook(_xlsx(build), "m.xlsx").sheets[0].rows
    assert [cells[0] for _, cells in rows] == [("A", "Carton 1")] * 3


def test_billing_is_20_rows_a_page_minimum_one_and_empty_is_free():
    def build(wb):
        a = wb.active
        a.title = "big"
        for i in range(41):
            a.append([f"row {i}"])
        wb.create_sheet("small")["A1"] = "one row"
        wb.create_sheet("empty")
    wb = read_workbook(_xlsx(build), "b.xlsx")
    assert {s.name: s.billable_pages for s in wb.sheets} == {"big": 3, "small": 1, "empty": 0}


def test_a_long_sheet_is_cut_at_the_row_limit_and_says_so(monkeypatch):
    monkeypatch.setattr(et, "MAX_ROWS", 10)
    def build(wb):
        for i in range(25):
            wb.active.append([i + 1])
    sheet = read_workbook(_xlsx(build), "long.xlsx").sheets[0]
    assert sheet.data_rows == 10 and sheet.truncated
    assert "cut at 10 rows" in sheet_text(sheet)


def test_classification_preview_reads_only_the_first_rows():
    def build(wb):
        for i in range(100):
            wb.active.append([f"line {i + 1}"])
    sheet = read_workbook(_xlsx(build), "p.xlsx").sheets[0]
    preview = sheet_text(sheet, max_rows=60)
    assert "line 60" in preview and "line 61" not in preview and "(40 more rows)" in preview


def test_line_item_chunks_each_repeat_the_header_rows():
    def build(wb):
        ws = wb.active
        ws.append(["Invoice No", "X1"])
        ws.append([])
        ws.append(["Part", "Qty"])
        for i in range(450):
            ws.append([f"P{i}", i])
    sheet = read_workbook(_xlsx(build), "c.xlsx").sheets[0]
    chunks = sheet_chunks(sheet, rows_per_chunk=200, header_rows=2)
    assert len(chunks) == 3
    assert all("A: Part | B: Qty" in c and "A: Invoice No" in c for c in chunks)
    body = [line for c in chunks for line in c.splitlines() if "A: P" in line and "Part" not in line]
    assert len(body) == 450                                                # nothing lost or doubled


def test_a_password_protected_xlsx_is_reported_as_locked():
    fake = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64 + "EncryptionInfo".encode("utf-16-le") + b"\0" * 64
    with pytest.raises(WorkbookLocked):
        read_workbook(fake, "locked.xlsx")


def test_a_damaged_file_is_reported_not_crashed():
    with pytest.raises(WorkbookUnreadable):
        read_workbook(b"not excel", "bad.xlsx")
    with pytest.raises(WorkbookUnreadable):
        read_workbook(b"not excel", "bad.xls")


def test_an_xlsx_saved_with_the_old_xls_extension_is_still_read():
    wb = read_workbook(_xlsx(_invoice), "renamed.xls")
    assert wb.sheets[0].name == "Detail"
