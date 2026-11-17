"""The CTH dump's own rows, for Tenant Admin -> Master Data -> CTH Master.

The dump stays exactly what it is - the customer's material master workbook, one per template
group, read by material_master.py for every job's CTH lookup. Nothing here changes how a job
finds its CTH. This module adds two things around that file:

1. A row listing. material_master only keeps a key -> code map (good for lookups, useless for
   showing the sheet), so the rows (part code, description, CTH) are parsed into a sidecar
   "<group>.rows.json" in the background, keyed on the workbook's identity like the map is.

2. A SAFE replace. An upload is written aside first ("incoming/<group>.<ext>") and checked off
   the request thread - opening a 9 MB workbook takes ~30 s, and doing that inside a request
   was observed to get the worker killed (see material_master.start_background_parse). Only a
   sheet with a code column and a part-code or description column replaces the live dump; a
   bad one leaves the old dump in place for jobs and records why ("incoming/<group>.error").

Sidecar names end in ".json" / ".building" on purpose: material_master.master_path skips those
suffixes when it looks for the workbook.
"""
import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core import material_master as mm

logger = logging.getLogger(__name__)

_ROWS_VERSION = 1
# group_id -> (stamp, rows). Rows are [part_code, description, cth_as_written, cth_clean].
_ROWS: dict[str, tuple[tuple, list[list[str]]]] = {}
_LOCK = threading.Lock()


def _rows_file(group_id: str) -> Path:
    return mm.master_path(group_id).parent / f"{group_id}.rows.json"


def _rows_marker(group_id: str) -> Path:
    return mm.master_path(group_id).parent / f"{group_id}.rows.building"


def _incoming_dir() -> Path:
    return mm.master_path("x").parent / "incoming"


def _incoming_files(group_id: str) -> list[Path]:
    d = _incoming_dir()
    return sorted(p for p in d.glob(f"{group_id}.*") if p.suffix.lower() in (".xlsx", ".xlsm")) if d.exists() else []


def _error_file(group_id: str) -> Path:
    return _incoming_dir() / f"{group_id}.error"


def _stamp(group_id: str) -> tuple | None:
    path = mm.master_path(group_id)
    return (mm._stamp(path) + (_ROWS_VERSION,)) if path.exists() else None


def _columns(headers: list[str]) -> tuple[int | None, int | None, int | None]:
    """(part code, description, CTH) column indexes - the same heading hints material_master
    uses for lookups, so this page shows exactly the columns jobs read."""
    code_i = mm._pick(headers, mm._CODE_HINTS, exclude=("desc",))
    mat_i = mm._pick(headers, mm._MATERIAL_HINTS, exclude=("desc",))
    desc_i = mm._pick(headers, ("desc",))
    return mat_i, desc_i, code_i


def _cell(row, i) -> str:
    if i is None or i >= len(row) or row[i] is None:
        return ""
    value = row[i]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _parse_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        headers = [str(h or "") for h in next(it, ())]
        mat_i, desc_i, code_i = _columns(headers)
        if code_i is None or (mat_i is None and desc_i is None):
            raise ValueError(
                "Row 1 needs a CTH column (a heading with 'code', 'CTH', 'HSN' or 'comm') and a part "
                "code or description column. Found: " + ", ".join(h for h in headers if h.strip())[:300])
        rows = []
        for row in it:
            raw = _cell(row, code_i)
            part, desc = _cell(row, mat_i), _cell(row, desc_i)
            if not raw and not part and not desc:
                continue
            rows.append([part, desc, raw, mm.clean_code(raw)])
        return [h.strip() for h in headers if h.strip()], rows
    finally:
        wb.close()


def build_rows(group_id: str) -> None:
    """Parse the live dump's rows into the sidecar. Safe to call from a background thread only."""
    stamp = _stamp(group_id)
    if stamp is None:
        return
    headers, rows = _parse_rows(mm.master_path(group_id))
    _rows_file(group_id).write_text(json.dumps({"stamp": list(stamp), "headers": headers, "rows": rows}), encoding="utf-8")
    with _LOCK:
        _ROWS[group_id] = (stamp, rows)


def start_rows_build(group_id: str) -> None:
    marker = _rows_marker(group_id)
    if marker.exists() or not mm.master_path(group_id).exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")

    def _run() -> None:
        try:
            build_rows(group_id)
        except Exception:  # noqa: BLE001
            logger.exception("could not list the rows of the CTH dump for %s", group_id)
        finally:
            marker.unlink(missing_ok=True)

    threading.Thread(target=_run, daemon=True, name=f"cth-rows-{group_id}").start()


def cached_rows(group_id: str) -> list[list[str]] | None:
    """The live dump's rows if already parsed, else None (and a background build is started)."""
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
            rows = blob["rows"]
            with _LOCK:
                _ROWS[group_id] = (stamp, rows)
            return rows
    except (OSError, ValueError, KeyError):
        pass
    start_rows_build(group_id)
    return None


def cached_headers(group_id: str) -> list[str]:
    """The live dump's column headings from the parsed copy, or [] if not parsed yet."""
    try:
        blob = json.loads(_rows_file(group_id).read_text(encoding="utf-8"))
        if tuple(blob.get("stamp") or ()) == _stamp(group_id):
            return list(blob.get("headers") or [])
    except (OSError, ValueError):
        pass
    return []


def status(group_id: str) -> dict:
    """What the page shows above the table: the live file, and any upload still being checked
    or rejected."""
    path = mm.master_path(group_id)
    out: dict = {"attached": path.exists(), "file_name": None, "uploaded_at": None,
                 "checking_upload": bool(_incoming_files(group_id)), "upload_error": None}
    if path.exists():
        out["file_name"] = mm.original_name(group_id) or path.name
        out["uploaded_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    err = _error_file(group_id)
    if err.exists():
        out["upload_error"] = err.read_text(encoding="utf-8")
    return out


def accept_upload(group_id: str, filename: str, content: bytes) -> None:
    """Write the new dump aside and check/replace it in the background (see module docstring)."""
    d = _incoming_dir()
    d.mkdir(parents=True, exist_ok=True)
    for stale in d.glob(f"{group_id}.*"):
        stale.unlink(missing_ok=True)
    incoming = d / f"{group_id}{Path(filename).suffix.lower()}"
    incoming.write_bytes(content)
    (d / f"{group_id}.name").write_text(filename, encoding="utf-8")

    def _run() -> None:
        try:
            headers, rows = _parse_rows(incoming)   # ValueError for a sheet jobs could not use
            _replace_live(group_id, incoming, filename, headers, rows)
        except ValueError as exc:
            _error_file(group_id).write_text(f"{filename}: {exc} The previous dump is still in use.", encoding="utf-8")
            incoming.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("checking the uploaded CTH dump for %s failed", group_id)
            _error_file(group_id).write_text(
                f"{filename}: could not be read ({exc.__class__.__name__}). The previous dump is still in use.",
                encoding="utf-8")
            incoming.unlink(missing_ok=True)
        finally:
            (d / f"{group_id}.name").unlink(missing_ok=True)

    threading.Thread(target=_run, daemon=True, name=f"cth-upload-{group_id}").start()


def _replace_live(group_id: str, incoming: Path, filename: str, headers: list[str], rows: list[list[str]]) -> None:
    """Swap the checked file in exactly the way the existing upload endpoint does, then rebuild
    the lookup map and this page's rows."""
    path = mm.master_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in path.parent.glob(f"{group_id}.*"):
        try:
            stale.unlink()
        except OSError:
            logger.warning("could not remove the previous material master %s", stale)
    target = path.with_suffix(incoming.suffix)
    shutil.move(str(incoming), str(target))
    mm.remember_name(group_id, filename)
    mm.clear_cache(group_id)
    # This page's listing, from the parse just done - no second 30-second read of the file.
    stamp = _stamp(group_id)
    _rows_file(group_id).write_text(json.dumps({"stamp": list(stamp), "headers": headers, "rows": rows}), encoding="utf-8")
    with _LOCK:
        _ROWS[group_id] = (stamp, rows)
    mm.start_background_parse(group_id)   # the lookup map jobs use
