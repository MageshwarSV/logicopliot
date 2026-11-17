"""The CTH dump as a small database, for Tenant Admin -> Master Data -> CTH Master.

The dump stays exactly what it is - the customer's material master workbook, one per template
group, which material_master.py reads for every job's CTH lookup. The workbook IS the data:
there is no database copy. This module lets the page:

- list its rows (part code, description, CTH) from a parsed sidecar "<group>.rows.json",
  built in the background (a 9 MB sheet takes ~30 s to open, and doing that inside a request
  was observed to get the worker killed - see material_master.start_background_parse);
- edit one row, or add a few rows, by WRITING THE WORKBOOK: the whole sheet is rewritten from
  the parsed copy (every original column kept, e.g. "HS CODE MISSING/UPDATED IN ODOO"), swapped
  in atomically, and the jobs' lookup map is rebuilt. The previous file is kept in backups/.

A full replacement of the dump is NOT done from here - that stays a backend / Super Admin job
(template_groups.upload_material_master).

Sidecars: names ending ".json" are skipped by material_master.master_path when it looks for the
workbook; locks, backups and temp files live in subfolders so its "<group>.*" glob never sees them.
"""
import json
import logging
import os
import shutil
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.core import material_master as mm

logger = logging.getLogger(__name__)

_ROWS_VERSION = 2
KEEP_BACKUPS = 10
MAX_ADD_ROWS = 500
# group_id -> (stamp, parsed). parsed = {"header_row": [...], "cols": [mat, desc, code], "rows": [[raw cells]]}
_ROWS: dict[str, tuple[tuple, dict]] = {}
_LOCK = threading.Lock()


class DumpError(Exception):
    """Something the Tenant Admin should be told (bad input, row changed, dump not ready)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- paths

def _dir() -> Path:
    return mm.master_path("x").parent


def _rows_file(group_id: str) -> Path:
    return _dir() / f"{group_id}.rows.json"


def _rows_marker(group_id: str) -> Path:
    return _dir() / f"{group_id}.rows.building"


def _sub(name: str) -> Path:
    d = _dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp(group_id: str) -> tuple | None:
    path = mm.master_path(group_id)
    return (mm._stamp(path) + (_ROWS_VERSION,)) if path.exists() else None


# ---------------------------------------------------------------- parsing

def _columns(headers: list[str]) -> tuple[int | None, int | None, int | None]:
    """(part code, description, CTH) column indexes - the same heading hints material_master
    uses for lookups, so this page shows and writes exactly the columns jobs read."""
    code_i = mm._pick(headers, mm._CODE_HINTS, exclude=("desc",))
    mat_i = mm._pick(headers, mm._MATERIAL_HINTS, exclude=("desc",))
    desc_i = mm._pick(headers, ("desc",))
    return mat_i, desc_i, code_i


def _json_cell(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(_json_cell(value)).split())


def _parse(path: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header_row = [_json_cell(h) for h in next(it, ())]
        mat_i, desc_i, code_i = _columns([str(h or "") for h in header_row])
        if code_i is None or (mat_i is None and desc_i is None):
            raise DumpError("The dump has no CTH column and part code / description column in row 1.")
        rows = []
        for row in it:
            cells = [_json_cell(c) for c in row]
            if not any(c not in (None, "") for c in cells):
                continue
            rows.append(cells)
        return {"header_row": header_row, "cols": [mat_i, desc_i, code_i], "rows": rows}
    finally:
        wb.close()


def _save_sidecar(group_id: str, parsed: dict) -> None:
    stamp = _stamp(group_id)
    _rows_file(group_id).write_text(json.dumps({"stamp": list(stamp), **parsed}), encoding="utf-8")
    with _LOCK:
        _ROWS[group_id] = (stamp, parsed)


def build_rows(group_id: str) -> None:
    if mm.master_path(group_id).exists():
        _save_sidecar(group_id, _parse(mm.master_path(group_id)))


def start_rows_build(group_id: str) -> None:
    marker = _rows_marker(group_id)
    if marker.exists() or not mm.master_path(group_id).exists():
        return
    marker.write_text("", encoding="utf-8")

    def _run() -> None:
        try:
            build_rows(group_id)
        except Exception:  # noqa: BLE001
            logger.exception("could not list the rows of the CTH dump for %s", group_id)
        finally:
            marker.unlink(missing_ok=True)

    threading.Thread(target=_run, daemon=True, name=f"cth-rows-{group_id}").start()


def cached(group_id: str) -> dict | None:
    """The live dump, parsed, if ready; else None (and a background read is started)."""
    stamp = _stamp(group_id)
    if stamp is None:
        return None
    with _LOCK:
        hit = _ROWS.get(group_id)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        blob = json.loads(_rows_file(group_id).read_text(encoding="utf-8"))
        if tuple(blob.get("stamp") or ()) == stamp:
            parsed = {k: blob[k] for k in ("header_row", "cols", "rows")}
            with _LOCK:
                _ROWS[group_id] = (stamp, parsed)
            return parsed
    except (OSError, ValueError, KeyError):
        pass
    start_rows_build(group_id)
    return None


def view(parsed: dict) -> list[dict]:
    """Each data row as the page shows it. `row` is its position in the sheet (row 2 = first)."""
    mat_i, desc_i, code_i = parsed["cols"]

    def at(cells, i):
        return _text(cells[i]) if i is not None and i < len(cells) else ""

    out = []
    for n, cells in enumerate(parsed["rows"]):
        raw = at(cells, code_i)
        out.append({"row": n + 2, "part_code": at(cells, mat_i), "description": at(cells, desc_i),
                    "cth_as_written": raw, "cth": mm.clean_code(raw) or raw})
    return out


def status(group_id: str) -> dict:
    path = mm.master_path(group_id)
    out = {"attached": path.exists(), "file_name": None, "uploaded_at": None}
    if path.exists():
        out["file_name"] = mm.original_name(group_id) or path.name
        out["uploaded_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return out


# ---------------------------------------------------------------- writing

def clean_cth(value) -> str:
    if len("".join(ch for ch in str(value or "") if ch.isdigit())) != 8:
        raise DumpError(f"CTH must be 8 digits (got \"{value or ''}\").")
    return mm.clean_code(value)


class _FileLock:
    """One writer per dump across every worker process."""

    def __init__(self, group_id: str):
        self.path = _sub("locks") / group_id

    def __enter__(self):
        import fcntl

        self.fh = open(self.path, "w")
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() > deadline:
                    self.fh.close()
                    raise DumpError("Another change to this dump is still being saved. Try again in a moment.", 409)
                time.sleep(0.2)

    def __exit__(self, *exc):
        import fcntl

        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _write(group_id: str, parsed: dict) -> None:
    """Rewrite the workbook from `parsed`, keep the old one in backups/, swap atomically, then
    refresh this page's copy and the jobs' lookup map."""
    import openpyxl

    live = mm.master_path(group_id)
    tmp = _sub("tmp") / f"{group_id}{live.suffix}"
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Sheet1")
    ws.append(parsed["header_row"])
    for cells in parsed["rows"]:
        ws.append(cells)
    wb.save(tmp)

    backups = _sub("backups")
    shutil.copy2(live, backups / f"{group_id}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{live.suffix}")
    for old in sorted(backups.glob(f"{group_id}-*"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)

    os.replace(tmp, live)
    _save_sidecar(group_id, parsed)
    mm.clear_cache(group_id)
    mm.start_background_parse(group_id)


def _ready(group_id: str) -> dict:
    if not mm.master_path(group_id).exists():
        raise DumpError("No CTH dump has been uploaded for this template yet.", 404)
    parsed = cached(group_id)
    if parsed is None:
        raise DumpError("The dump is still being read. Try again in a minute.", 409)
    return parsed


def _part_index(parsed: dict) -> dict[str, int]:
    mat_i = parsed["cols"][0]
    if mat_i is None:
        return {}
    return {mm._norm(_text(c[mat_i])): n for n, c in enumerate(parsed["rows"])
            if mat_i < len(c) and _text(c[mat_i])}


def _set(cells: list, i: int | None, value: str) -> None:
    if i is None:
        return
    while len(cells) <= i:
        cells.append(None)
    cells[i] = value


def update_row(group_id: str, row: int, expected: dict, values: dict) -> dict:
    """Edit one row. `expected` is what the page showed; if the row no longer holds that
    (someone else changed it, or the dump was replaced), nothing is written."""
    part = " ".join(str(values.get("part_code") or "").split())
    desc = " ".join(str(values.get("description") or "").split())
    code = clean_cth(values.get("cth"))
    if not part and not desc:
        raise DumpError("Give a Part Code or a Description.")
    with _FileLock(group_id):
        parsed = _ready(group_id)
        rows = [list(r) for r in parsed["rows"]]
        n = row - 2
        if n < 0 or n >= len(rows):
            raise DumpError("That row no longer exists. Refresh the page.", 409)
        shown = view({**parsed, "rows": [parsed["rows"][n]]})[0]
        if any(shown[k] != (expected.get(k) or "") for k in ("part_code", "description", "cth")):
            raise DumpError("This row was changed since you opened it. Refresh the page and try again.", 409)
        if part and mm._norm(part) != mm._norm(shown["part_code"]):
            other = _part_index(parsed).get(mm._norm(part))
            if other is not None and other != n:
                raise DumpError(f"Part Code {part} is already in the dump (row {other + 2}).", 409)
        mat_i, desc_i, code_i = parsed["cols"]
        _set(rows[n], mat_i, part)
        _set(rows[n], desc_i, desc)
        _set(rows[n], code_i, code)
        new = {**parsed, "rows": rows}
        _write(group_id, new)
        out = view({**new, "rows": [rows[n]]})[0]
        out["row"] = row
        return out


def add_rows(group_id: str, items: list[dict]) -> dict:
    """Append new rows. A row is skipped (with the reason) when its CTH is not 8 digits, it has
    neither part code nor description, or its part code is already in the dump."""
    if not items:
        raise DumpError("No rows to add.")
    if len(items) > MAX_ADD_ROWS:
        raise DumpError(f"Add at most {MAX_ADD_ROWS} rows at a time. A full update of the dump is done by the backend team.")
    with _FileLock(group_id):
        parsed = _ready(group_id)
        rows = [list(r) for r in parsed["rows"]]
        width = len(parsed["header_row"])
        mat_i, desc_i, code_i = parsed["cols"]
        parts = _part_index(parsed)
        added, skipped = [], []
        for k, item in enumerate(items, start=1):
            part = " ".join(str(item.get("part_code") or "").split())
            desc = " ".join(str(item.get("description") or "").split())
            label = item.get("label") or f"Row {k}"
            try:
                code = clean_cth(item.get("cth"))
            except DumpError as exc:
                skipped.append({"row": label, "part_code": part, "reason": str(exc)})
                continue
            if not part and not desc:
                skipped.append({"row": label, "part_code": part, "reason": "No Part Code or Description."})
                continue
            if part and mm._norm(part) in parts:
                skipped.append({"row": label, "part_code": part,
                                "reason": f"Part Code already in the dump (row {parts[mm._norm(part)] + 2}). Edit that row instead."})
                continue
            cells: list = [None] * width
            _set(cells, mat_i, part)
            _set(cells, desc_i, desc)
            _set(cells, code_i, code)
            rows.append(cells)
            if part:
                parts[mm._norm(part)] = len(rows) - 1
            added.append({"row": len(rows) + 1, "part_code": part, "description": desc, "cth": code})
        if added:
            _write(group_id, {**parsed, "rows": rows})
        return {"added": len(added), "skipped": skipped, "added_rows": added}


TEMPLATE_HEADERS = ["Part Code", "Description", "CTH"]


def read_add_file(content: bytes) -> list[dict]:
    """Rows from the 3-column template (Part Code, Description, CTH), found by heading."""
    import io

    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise DumpError(f"Could not read that workbook: {exc}") from exc
    try:
        it = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        header = [_text(h).lower() for h in next(it, ())]
        try:
            idx = [header.index(h.lower()) for h in TEMPLATE_HEADERS]
        except ValueError:
            raise DumpError("Row 1 must be: Part Code, Description, CTH (download the Template).") from None
        items = []
        for n, row in enumerate(it, start=2):
            cells = list(row or ())
            vals = [_text(cells[i]) if i < len(cells) else "" for i in idx]
            if not any(vals):
                continue
            items.append({"label": f"Excel row {n}", "part_code": vals[0], "description": vals[1], "cth": vals[2]})
            if len(items) > MAX_ADD_ROWS:
                break
        return items
    finally:
        wb.close()
