"""The job's ERP Excel edited in the app: read as sheets, saved back keeping each column's type,
then used instead of the generated workbook (download and ERP import) until reset."""
from datetime import datetime

import openpyxl

from app.core import erp_excel_edit as ee


def _book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "INVOICES"
    ws.append(["InvSrNo", "Invoice_No", "Invoice_Date", "Product_Value"])
    ws.append([1, "INV-1", datetime(2026, 11, 11), 1250.5])
    ws.append([2, "INV-2", datetime(2026, 11, 12), 99])
    wb.create_sheet("GENERAL").append(["BETypeCode", "Importer"])
    wb.save(path)
    return path


def test_read_edit_save_keeps_types_and_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "edited_dir", lambda job_id: tmp_path / "edited" / job_id)
    base = _book(tmp_path / "JOB-1.xlsx")
    sheets = ee.read_sheets(base)
    inv = next(s for s in sheets if s["name"] == "INVOICES")
    assert inv["rows"][1] == ["1", "INV-1", "2026-11-11", "1250.5"]

    inv["rows"][1][3] = "1300.75"                 # change a value
    inv["rows"][2][2] = "15/11/2026"              # a date typed dd/mm/yyyy
    inv["rows"].append(["3", "INV-3", "2026-11-20", "10"])   # add a row
    gen = next(s for s in sheets if s["name"] == "GENERAL")
    gen["rows"].append(["H", "4S LOGISTIC SOLUTIONS"])
    out = ee.save_sheets(base, sheets, "job-1", "JOB-1.xlsx", saved_by="Vinoth")

    ws = openpyxl.load_workbook(out)["INVOICES"]
    assert ws.cell(2, 4).value == 1300.75 and isinstance(ws.cell(2, 4).value, float)
    assert ws.cell(3, 3).value == datetime(2026, 11, 15)
    assert ws.cell(4, 2).value == "INV-3" and ws.cell(4, 1).value == 3
    assert openpyxl.load_workbook(out)["GENERAL"].cell(2, 2).value == "4S LOGISTIC SOLUTIONS"
    assert ee.edited_meta("job-1")["saved_by"] == "Vinoth"

    # removing a row really removes it
    inv["rows"] = inv["rows"][:2]
    out = ee.save_sheets(out, sheets, "job-1", "JOB-1.xlsx", saved_by="Vinoth")
    assert openpyxl.load_workbook(out)["INVOICES"].max_row == 2


def test_the_edited_copy_replaces_the_build_until_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "edited_dir", lambda job_id: tmp_path / "edited" / job_id)
    built: list = []

    def build():
        built.append(1)
        return _book(tmp_path / "out" / "JOB-2.xlsx") if (tmp_path / "out").mkdir(exist_ok=True) is None else None

    assert ee.use_edited_or_build("job-2", tmp_path / "out", build).name == "JOB-2.xlsx" and built
    ee.save_sheets(_book(tmp_path / "base.xlsx"), ee.read_sheets(tmp_path / "base.xlsx"), "job-2", "JOB-2.xlsx", "x")
    built.clear()
    got = ee.use_edited_or_build("job-2", tmp_path / "run", build)
    assert not built and got.parent == tmp_path / "run"            # the edited copy, not a build
    ee.reset("job-2")
    assert ee.edited_file("job-2") is None and ee.edited_meta("job-2") is None


def test_header_row_never_changes_and_lock_rules(tmp_path, monkeypatch):
    import openpyxl
    from types import SimpleNamespace
    from app.core import erp_excel_edit as e

    monkeypatch.setattr(e, "edited_dir", lambda job_id: tmp_path / "ed" / job_id)
    base = tmp_path / "base.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    ws.append(["Qty", "Name"])
    ws.append([5, "a"])
    wb.save(base)
    out = e.save_sheets(base, [{"name": "S", "rows": [["HACKED", "X"], ["7", "b"], ["8", "c"]]}],
                        "j1", "J.xlsx", "tester")
    rows = e.read_sheets(out)[0]["rows"]
    assert rows[0] == ["Qty", "Name"]
    assert rows[1:] == [["7", "b"], ["8", "c"]]
    assert openpyxl.load_workbook(out).active.cell(2, 1).value == 7

    assert e.locked_reason(SimpleNamespace(gk2_status="entering_erp", erp_status=None))
    assert e.locked_reason(SimpleNamespace(gk2_status="approved", erp_status="ok"))
    assert e.locked_reason(SimpleNamespace(gk2_status=None, erp_status="error")) is None
    assert e.locked_reason(SimpleNamespace(gk2_status=None, erp_status=None)) is None
