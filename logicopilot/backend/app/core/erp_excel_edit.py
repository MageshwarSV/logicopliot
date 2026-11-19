"""The job's ERP-import workbook, opened and edited inside the application.

Normally the workbook is rebuilt from the job's data every time it is needed (Download as Excel,
Final Submit / GK2 approval, an ERP run). Once someone edits it in the app and saves, the edited
copy REPLACES the generated one everywhere - download and the real ERP import - until it is
reset (agreed 2026-11-19). Field corrections made after the save are therefore not in it until
"Reset to generated" is pressed; the screen says so.

Stored beside the job's other files: uploads/job_excel_edited/{job id}/{name}.xlsx plus meta.json
(who saved it, when). No database column - the file existing IS the switch.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MAX_ROWS = 2000
MAX_COLS = 300


def edited_dir(job_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "job_excel_edited" / job_id


def edited_file(job_id: str) -> Path | None:
    d = edited_dir(job_id)
    if not d.exists():
        return None
    return next(iter(sorted(d.glob("*.xlsx"))), None)


def edited_meta(job_id: str) -> dict | None:
    f = edited_file(job_id)
    if f is None:
        return None
    try:
        meta = json.loads((edited_dir(job_id) / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    return {"saved_by": meta.get("saved_by"), "saved_at": meta.get("saved_at"), "file": f.name}


def locked_reason(job) -> str | None:
    """Why the Excel can no longer be changed: it is being entered into the ERP right now, or
    the ERP already has it. Opening and downloading stay allowed."""
    if (job.gk2_status or "") in ("preparing_erp", "entering_erp"):
        return "The ERP entry is running now - the Excel can't be changed."
    if (job.erp_status or "") == "ok":
        return "This job is already in the ERP - changing the Excel here would not reach it."
    return None


def reset(job_id: str) -> None:
    shutil.rmtree(edited_dir(job_id), ignore_errors=True)


def use_edited_or_build(job_id: str, out_dir: Path, build) -> Path:
    """The workbook to hand over: the saved edited copy (copied into `out_dir`, so the caller's
    folder is used exactly as before), or `build()` when there is none."""
    f = edited_file(job_id)
    if f is None:
        return build()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f.name
    if dest.resolve() != f.resolve():
        shutil.copy(f, dest)
    logger.info("job %s: using the edited ERP Excel (%s)", job_id, f.name)
    return dest


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def read_sheets(path: Path) -> list[dict]:
    """[{"name", "rows": [[cell text, ...], ...]}] - every sheet, trailing empty rows/columns
    trimmed."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=False)
    out = []
    try:
        for ws in wb.worksheets:
            rows = [[_text(c) for c in r[:MAX_COLS]] for r in ws.iter_rows(max_row=min(ws.max_row, MAX_ROWS), values_only=True)]
            while rows and not any(rows[-1]):
                rows.pop()
            width = max((max((i + 1 for i, c in enumerate(r) if c != ""), default=0) for r in rows), default=0)
            out.append({"name": ws.title, "rows": [r[:width] + [""] * (width - len(r[:width])) for r in rows]})
    finally:
        wb.close()
    return out


_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")


def _typed(text: str, like):
    """The cell value for `text`, keeping the type the column already had (a number stays a
    number, a date a date) - the ERP import reads types, not just text."""
    t = (text or "").strip()
    if t == "":
        return None
    if isinstance(like, (int, float)) and not isinstance(like, bool) and _NUM.match(t):
        return int(t) if "." not in t else float(t)
    if isinstance(like, (datetime, date)):
        try:
            if _ISO.match(t):
                return datetime.fromisoformat(t)
            m = _DMY.match(t)
            if m:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return text


def save_sheets(base: Path, sheets: list[dict], job_id: str, file_name: str, saved_by: str | None) -> Path:
    """Write `sheets` (as read_sheets returns them) onto a copy of `base` - keeping its sheets,
    column widths and formats - and keep it as the job's edited workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(base)
    try:
        by_name = {s.get("name"): s.get("rows") or [] for s in sheets}
        for ws in wb.worksheets:
            if ws.title not in by_name:
                continue
            rows = [list(r)[:MAX_COLS] for r in by_name[ws.title][:MAX_ROWS]]
            if not rows:
                rows = [[]]
            # Only as wide as the sheet's own columns - no new columns past the headers.
            width = ws.max_column
            # A column's type, from the first data row that has a value in it.
            col_like = {}
            for c in range(1, width + 1):
                for r in range(2, min(ws.max_row, 50) + 1):
                    v = ws.cell(r, c).value
                    if v is not None:
                        col_like[c] = v
                        break
            for r_idx, row in enumerate(rows, start=1):
                if r_idx == 1:
                    # The header row is never changed - the ERP import maps columns by it.
                    continue
                for c_idx in range(1, width + 1):
                    text = row[c_idx - 1] if c_idx - 1 < len(row) else ""
                    cell = ws.cell(r_idx, c_idx)
                    like = cell.value if cell.value is not None else col_like.get(c_idx)
                    cell.value = _typed(str(text), like)
            if ws.max_row > len(rows):
                ws.delete_rows(len(rows) + 1, ws.max_row - len(rows))
        d = edited_dir(job_id)
        reset(job_id)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / file_name
        wb.save(dest)
    finally:
        wb.close()
    (d / "meta.json").write_text(json.dumps({"saved_by": saved_by,
                                             "saved_at": datetime.now(timezone.utc).isoformat()}))
    return dest
