"""Onboarding Step 1 - read a customer's ERP Excel workbook: every worksheet and its columns.

Nothing here is hardcoded to a customer: sheet names and column headings come from the file.
The workbook is read, never written. Suggestions (a readable label, a data type from the values
under the heading) are only suggestions - the admin reviews them. Classification into
Document / Calculated / ERP / Contact is always the admin's choice, never guessed from a name.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

HEADER_SCAN_ROWS = 15     # the heading row is looked for in the first rows (titles/notes above it)
SAMPLE_ROWS = 5           # values under the heading used to suggest a data type
DATA_TYPES = ("text", "number", "decimal", "date", "boolean")
CATEGORIES = ("document", "calculated", "erp", "contact")


def _openpyxl():
    try:
        import openpyxl  # noqa: PLC0415
        return openpyxl
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Reading Excel needs the openpyxl package on the server.") from exc


def column_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def detect_header_row(rows: list[list]) -> int:
    """The 1-based row holding the headings: the first row with the most text cells among the
    first rows - a title or a note above the headings has fewer. 1 for an empty sheet."""
    best, best_count = 1, 0
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS], start=1):
        count = sum(1 for v in row if isinstance(v, str) and v.strip())
        if count > best_count:
            best, best_count = i, count
    return best


def suggest_label(name: str) -> str:
    """'Invoice_No' -> 'Invoice No', 'InvSrNo' -> 'Inv Sr No'. Only a suggestion."""
    s = re.sub(r"[_\-.]+", " ", name or "").strip()
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    return re.sub(r"\s+", " ", s)


def suggest_type(samples: list) -> str:
    """A data type from the values under a heading; text when there is nothing to go on."""
    vals = [v for v in samples if v not in (None, "")]
    if not vals:
        return "text"
    if all(isinstance(v, (datetime, date)) for v in vals):
        return "date"
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number" if all(float(v).is_integer() for v in vals) else "decimal"
    return "text"


def _sample_text(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)[:60]


def sheet_columns(rows: list[list], header_row: int) -> list[dict]:
    """The columns of one sheet from its rows (values only). A blank heading in the middle is
    kept (an ERP template's spacer column - dropping it would shift every column after it);
    trailing blanks are padding and dropped."""
    if header_row < 1 or header_row > len(rows):
        return []
    header = rows[header_row - 1]
    below = rows[header_row:header_row + SAMPLE_ROWS]
    cols: list[dict] = []
    for idx, value in enumerate(header, start=1):
        samples = [r[idx - 1] if idx - 1 < len(r) else None for r in below]
        cols.append({
            "key": column_letter(idx),
            "index": idx,
            "name": _text(value),
            "samples": [_sample_text(s) for s in samples if s not in (None, "")][:3],
            "suggested_type": suggest_type(samples),
        })
    while cols and not cols[-1]["name"]:
        cols.pop()
    return cols


def read_workbook(path: str | Path, header_rows: dict[str, int] | None = None) -> list[dict]:
    """Every worksheet: [{"name", "header_row", "columns": [...]}] in workbook order.
    header_rows overrides the detected heading row per sheet (the admin's correction)."""
    xl = _openpyxl()
    wb = xl.load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        sheets = []
        for ws in wb.worksheets:
            limit = HEADER_SCAN_ROWS + SAMPLE_ROWS + 1
            override = (header_rows or {}).get(ws.title)
            if override:
                limit = max(limit, override + SAMPLE_ROWS)
            rows = [list(r) for r in ws.iter_rows(min_row=1, max_row=limit, values_only=True)]
            header_row = override or detect_header_row(rows)
            sheets.append({"name": ws.title, "header_row": header_row,
                           "columns": sheet_columns(rows, header_row)})
        return sheets
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
