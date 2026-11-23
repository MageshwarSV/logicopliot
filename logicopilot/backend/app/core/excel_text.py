"""Read an Excel workbook's cells as clean table text - for classifying and extracting from Excel
directly, instead of converting it to a PDF and reading it back as images.

Why: a converted sheet is read by OCR/vision, so numbers can be misread and a long line-item
table is cut across pages. The cells themselves are exact.

Text format, one block per visible sheet, only non-empty cells, each with its reference:

    === SHEET: Detail ===
    R1 | A: Invoice No | B: MES2620601085
    R5 | A: Part No | B: Description | C: Qty | D: Amount
    R6 | A: K2839-01 | B: Capacitor | C: 1000 | D: 125.5

so an extracted value can be pointed back to its cell ("Detail!D6").

.xlsx/.xlsm via openpyxl (stored values, not formulas), .xls via xlrd. Hidden sheets are skipped.
A sheet is read up to MAX_ROWS rows; the rest is reported as truncated, never silently dropped.
"""
from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

EXCEL_EXTS = {".xlsx", ".xlsm", ".xls"}
MAX_ROWS = 5000              # rows read per sheet
MAX_COLS = 200               # columns read per sheet
ROWS_PER_BILLED_PAGE = 20    # billing: 20 rows of data = 1 page, minimum 1 per non-empty sheet
_MERGE_FILL_MAX_BYTES = 5 * 1024 * 1024   # above this, read in streaming mode (no merge fill)


class WorkbookLocked(Exception):
    """The workbook itself is password-protected - its cells cannot be read without one."""


class WorkbookUnreadable(Exception):
    """Not a workbook this can read (damaged, or not really Excel)."""


@dataclass
class Sheet:
    name: str
    # (excel row number, [(column letter, text value), ...]) - non-empty rows only
    rows: list[tuple[int, list[tuple[str, str]]]] = field(default_factory=list)
    truncated: bool = False          # more than MAX_ROWS rows / MAX_COLS columns

    @property
    def data_rows(self) -> int:
        return len(self.rows)

    @property
    def billable_pages(self) -> int:
        """20 rows of data = 1 page, minimum 1; an empty sheet is not billed."""
        return math.ceil(self.data_rows / ROWS_PER_BILLED_PAGE) if self.data_rows else 0


@dataclass
class Workbook:
    sheets: list[Sheet]               # visible sheets only, in workbook order
    hidden: list[str]                 # names of hidden sheets (skipped)


def _col_letter(idx: int) -> str:     # 1 -> A, 27 -> AA
    out = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0, 0) else value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if math.isfinite(value) and value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return " ".join(str(value).split())           # collapse newlines / runs of spaces


def _looks_encrypted_ooxml(data: bytes) -> bool:
    # A password-protected .xlsx is not a zip at all but an OLE compound file holding an
    # "EncryptionInfo" stream.
    return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" and b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00i\x00o\x00n\x00I\x00n\x00f\x00o" in data[:65536]


def read_workbook(data: bytes, filename: str) -> Workbook:
    ext = PurePosixPath(filename or "").suffix.lower()
    if ext == ".xls" and data[:4] == b"PK\x03\x04":
        ext = ".xlsx"                              # an .xlsx saved with the old extension
    if ext in (".xlsx", ".xlsm") and _looks_encrypted_ooxml(data):
        raise WorkbookLocked(filename)
    if ext in (".xlsx", ".xlsm"):
        return _read_openpyxl(data, filename)
    if ext == ".xls":
        return _read_xlrd(data, filename)
    raise WorkbookUnreadable(f"{filename}: not an Excel file")


def _read_openpyxl(data: bytes, filename: str) -> Workbook:
    import openpyxl

    streaming = len(data) > _MERGE_FILL_MAX_BYTES
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=streaming, data_only=True)
    except Exception as exc:  # noqa: BLE001 - zip/xml errors of every kind
        raise WorkbookUnreadable(f"{filename}: {exc}") from exc
    sheets: list[Sheet] = []
    hidden: list[str] = []
    try:
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                hidden.append(ws.title)
                continue
            merged: dict[tuple[int, int], object] = {}
            if not streaming:
                for rng in ws.merged_cells.ranges:
                    top = ws.cell(rng.min_row, rng.min_col).value
                    for r in range(rng.min_row, min(rng.max_row, MAX_ROWS) + 1):
                        for c in range(rng.min_col, min(rng.max_col, MAX_COLS) + 1):
                            merged[(r, c)] = top
            sheet = Sheet(name=ws.title)
            for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if r_idx > MAX_ROWS:
                    sheet.truncated = True
                    break
                cells = []
                for c_idx, v in enumerate(row, start=1):
                    if c_idx > MAX_COLS:
                        sheet.truncated = True
                        break
                    if v is None:
                        v = merged.get((r_idx, c_idx))
                    text = _fmt(v)
                    if text:
                        cells.append((_col_letter(c_idx), text))
                if cells:
                    sheet.rows.append((r_idx, cells))
            sheets.append(sheet)
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass
    return Workbook(sheets=sheets, hidden=hidden)


def _read_xlrd(data: bytes, filename: str) -> Workbook:
    import xlrd

    try:
        wb = xlrd.open_workbook(file_contents=data, formatting_info=True)
    except Exception as exc:  # noqa: BLE001
        if "encrypt" in str(exc).lower():
            raise WorkbookLocked(filename) from exc
        try:                                       # some writers produce files formatting_info rejects
            wb = xlrd.open_workbook(file_contents=data)
        except Exception as exc2:  # noqa: BLE001
            if "encrypt" in str(exc2).lower():
                raise WorkbookLocked(filename) from exc2
            raise WorkbookUnreadable(f"{filename}: {exc2}") from exc2
    sheets: list[Sheet] = []
    hidden: list[str] = []
    for sh in wb.sheets():
        if getattr(sh, "visibility", 0) != 0:
            hidden.append(sh.name)
            continue
        merged: dict[tuple[int, int], object] = {}
        for (rlo, rhi, clo, chi) in getattr(sh, "merged_cells", []) or []:
            top = sh.cell_value(rlo, clo)
            for r in range(rlo, min(rhi, MAX_ROWS)):
                for c in range(clo, min(chi, MAX_COLS)):
                    merged[(r, c)] = top
        sheet = Sheet(name=sh.name)
        if sh.nrows > MAX_ROWS or sh.ncols > MAX_COLS:
            sheet.truncated = True
        for r in range(min(sh.nrows, MAX_ROWS)):
            cells = []
            for c in range(min(sh.ncols, MAX_COLS)):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        v = xlrd.xldate_as_datetime(v, wb.datemode)
                    except Exception:  # noqa: BLE001
                        pass
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    v = bool(v)
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    v = merged.get((r, c))
                text = _fmt(v)
                if text:
                    cells.append((_col_letter(c + 1), text))
            if cells:
                sheet.rows.append((r + 1, cells))
        sheets.append(sheet)
    return Workbook(sheets=sheets, hidden=hidden)


def _row_line(row_no: int, cells: list[tuple[str, str]]) -> str:
    return f"R{row_no} | " + " | ".join(f"{col}: {val}" for col, val in cells)


def sheet_text(sheet: Sheet, max_rows: int | None = None) -> str:
    """The sheet as text - all of it, or its first `max_rows` data rows (for classifying)."""
    rows = sheet.rows if max_rows is None else sheet.rows[:max_rows]
    lines = [f"=== SHEET: {sheet.name} ==="] + [_row_line(r, c) for r, c in rows]
    if max_rows is not None and len(sheet.rows) > max_rows:
        lines.append(f"... ({len(sheet.rows) - max_rows} more rows)")
    elif sheet.truncated:
        lines.append(f"... (sheet cut at {MAX_ROWS} rows / {MAX_COLS} columns)")
    return "\n".join(lines)


def sheet_chunks(sheet: Sheet, rows_per_chunk: int = 200, header_rows: int = 3) -> list[str]:
    """The sheet split for reading line items: each chunk repeats the first `header_rows` data
    rows (the column headings, in practice), so every chunk can be read on its own."""
    if len(sheet.rows) <= rows_per_chunk:
        return [sheet_text(sheet)]
    head = sheet.rows[:header_rows]
    body = sheet.rows[header_rows:]
    chunks = []
    for start in range(0, len(body), rows_per_chunk):
        part = head + body[start:start + rows_per_chunk]
        chunks.append("\n".join([f"=== SHEET: {sheet.name} (rows part {len(chunks) + 1}) ==="]
                                + [_row_line(r, c) for r, c in part]))
    return chunks


ROW_CHUNK = 40          # line-item rows per AI request for an Excel table (see sheet_row_chunks)


def _is_number(text: str) -> bool:
    t = str(text or "").replace(",", "").strip()
    try:
        float(t)
        return True
    except ValueError:
        return False


def header_row_index(sheet: Sheet, scan: int = 80) -> int | None:
    """Index (into sheet.rows) of the table's column-heading row - "ITEM | PRODUCT NUMBER |
    DESCRIPTION ..." - or None. The heading row is all text, at least 3 cells, and the three
    rows after it are table rows: about as many cells, and each holding a number. A title or
    address block above the table never has three such rows straight after it."""
    rows = sheet.rows[:scan]
    for i, (_rn, cells) in enumerate(rows):
        texts = [v for _c, v in cells if str(v).strip()]
        if len(texts) < 3 or any(_is_number(v) for v in texts):
            continue
        after = rows[i + 1:i + 4]
        need = max(3, len(texts) // 2)
        if len(after) == 3 and all(
                len(c) >= need and any(_is_number(v) for _col, v in c) for _r, c in after):
            return i
    return None


def numbered_items(rows: list[tuple[int, list[tuple[str, str]]]]) -> int:
    """Rows that look like numbered line items: the first cell a whole number, 4+ cells.
    A TOTAL row (fewer cells, no item number) is not counted."""
    n = 0
    for _rn, cells in rows:
        if len(cells) >= 4 and str(cells[0][1]).strip().isdigit():
            n += 1
    return n


def sheet_row_chunks(sheet: Sheet, rows_per_chunk: int = ROW_CHUNK) -> list[tuple[str, int | None]]:
    """The sheet split for reading line items, as [(text, expected_rows_or_None)].

    One request per ~40 rows: a 110-line invoice sent whole came back with 5 lines repeated, one
    garbled and one stray copy (JOB-CEF1C1). Every part repeats everything above and including
    the real column-heading row (header_row_index - not just the first 3 rows, which on a
    commercial invoice are its title), so each part is read with its own column names.
    expected_rows: how many numbered item rows the part holds, given to the model as a count
    to keep to - only when the rows really are numbered (most of the table-looking rows)."""
    h = header_row_index(sheet)
    head = sheet.rows[:h + 1] if h is not None else sheet.rows[:3]
    body = sheet.rows[h + 1:] if h is not None else sheet.rows[3:]

    def hint(part):
        tableish = sum(1 for _r, c in part if len(c) >= 4)
        n = numbered_items(part)
        return n if n and n >= 0.8 * tableish else None

    if len(body) <= rows_per_chunk:
        return [(sheet_text(sheet), hint(body))]
    out = []
    for start in range(0, len(body), rows_per_chunk):
        part = body[start:start + rows_per_chunk]
        text = "\n".join([f"=== SHEET: {sheet.name} (rows part {len(out) + 1}) ==="]
                         + [_row_line(r, c) for r, c in head + part])
        out.append((text, hint(part)))
    return out


def find_cell(sheet: Sheet, value: str) -> str | None:
    """Where a value was read from, e.g. "Detail!D6" - exact match first, then contained."""
    want = " ".join(str(value or "").split())
    if not want:
        return None
    for exact in (True, False):
        for row_no, cells in sheet.rows:
            for col, text in cells:
                if (text == want) if exact else (want in text):
                    return f"{sheet.name}!{col}{row_no}"
    return None
