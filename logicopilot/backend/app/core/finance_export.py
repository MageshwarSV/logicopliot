"""CSV/XLSX export for the SaaS Finance Reports page. No generic tabular-export utility
existed anywhere in this codebase before this - CSV needs no new dependency (stdlib csv),
XLSX follows excel_entry.py's own lazy-import style (openpyxl is already a dependency for
the unrelated ERP-import feature, reused here rather than duplicated)."""
from __future__ import annotations

import csv
import io


def _openpyxl():
    try:
        import openpyxl  # noqa: PLC0415

        return openpyxl
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "openpyxl is not installed on this server - Excel export is unavailable."
        ) from exc


def rows_to_csv(headers: list[str], rows: list[list], *, keys: list[str] | None = None) -> bytes:
    """`rows` is a list of dicts (keyed by `keys`, same order as `headers`) or plain lists
    already in column order."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        if isinstance(row, dict):
            writer.writerow([row.get(k, "") for k in (keys or headers)])
        else:
            writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens non-ASCII (₹) cleanly


def rows_to_xlsx(sheet_name: str, headers: list[str], rows: list[list], *, keys: list[str] | None = None) -> bytes:
    xl = _openpyxl()
    wb = xl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31] or "Report"
    ws.append(headers)
    for row in rows:
        if isinstance(row, dict):
            ws.append([row.get(k, "") for k in (keys or headers)])
        else:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
