"""Indian Customs Tariff (ICEGATE) - the global CTH master. Phase 1: load, search, check.

Every 8-digit CTH the platform may use comes from here: across all tenants and all streams, never
per customer. The customer's own Reference CTH Dump (material_master.py) is untouched and keeps
answering part code / description -> CTH; this list decides which codes are ALLOWED.

The SaaS Admin uploads the whole list (Excel or CSV) and replaces it. Always in two steps:
preview_upload() shows what would change, apply_upload() writes it in one transaction. A code
missing from a new file is marked inactive, never deleted.

Columns are found by header: the code ("CTH", "CTH Code", "Tariff Item", "HS Code", ...), and
optionally Rate, Unit, Preferential Rate and Description. The first file (2026-11-26, "CTH
Tariff.new.xlsx") has no description - a later file can add one by simply carrying the column.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.customs_tariff import CustomsTariff, CustomsTariffUpload

CTH_LENGTH = 8
# A full tariff is ~12,700 codes. Far fewer means the wrong file (or a page of it): refused,
# since applying it would mark nearly the whole master inactive.
MIN_CODES = 1000
MAX_FILE_BYTES = 20 * 1024 * 1024
HEADER_SCAN_ROWS = 20

_CODE_HEADERS = ("cth", "tariff item", "tariff code", "tariff heading", "hs code", "hsn",
                 "itc hs", "itc-hs", "ritc", "tariff")
DATA_FIELDS = ("description", "rate", "unit", "preferential_rate")


class TariffError(Exception):
    """A problem the SaaS Admin should be told about (bad file, wrong columns)."""


def clean_cth(raw) -> str:
    """The 8-digit code, or "" when it is not one.

    "0101 21 00", "0101.21.00" and "01012100" are the same code. A number cell loses its
    leading zero in Excel (1012100), so a 7-digit NUMBER gets it back; a 7-digit TEXT is
    refused - nothing says where the missing digit belongs.
    """
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not raw.is_integer():
            return ""
        digits = str(int(raw))
        if len(digits) == CTH_LENGTH - 1:
            digits = "0" + digits
        return digits if len(digits) == CTH_LENGTH else ""
    text = str(raw).strip()
    if not text or not re.fullmatch(r"[\d\s.\-]+", text):
        return ""
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) == CTH_LENGTH else ""


def _cell(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _header_kind(text: str) -> str | None:
    h = " ".join(str(text or "").lower().replace("_", " ").split())
    if not h:
        return None
    if "prefer" in h:
        return "preferential_rate"
    if "desc" in h:
        return "description"
    if h in ("unit", "uqc") or h.startswith("unit") or "uqc" in h:
        return "unit"
    if "rate" in h or "duty" in h:
        return "rate"
    if any(k in h for k in _CODE_HEADERS):
        return "cth"
    return None


@dataclass
class ParsedFile:
    rows: dict[str, dict] = field(default_factory=dict)   # cth -> {description, rate, unit, preferential_rate}
    columns: list[str] = field(default_factory=list)      # the data columns the file carries
    skipped: list[dict] = field(default_factory=list)     # {"row": n, "value": ..., "reason": ...}


def _rows_of(content: bytes, file_name: str) -> list[tuple]:
    name = (file_name or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - any unreadable workbook is the same answer
            raise TariffError("Could not open that Excel file.") from exc
        try:
            ws = wb.worksheets[0]
            return [tuple(r) for r in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    if name.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        return [tuple(r) for r in csv.reader(io.StringIO(text))]
    raise TariffError("Upload the tariff as an Excel (.xlsx) or CSV file.")


def parse_file(content: bytes, file_name: str) -> ParsedFile:
    """Read the uploaded tariff file. Raises TariffError when it cannot be the tariff."""
    if len(content) > MAX_FILE_BYTES:
        raise TariffError("That file is too big for a tariff list (20 MB at most).")
    raw_rows = _rows_of(content, file_name)
    header_at, cols = None, {}
    for i, row in enumerate(raw_rows[:HEADER_SCAN_ROWS]):
        found: dict[str, int] = {}
        for j, cell in enumerate(row):
            kind = _header_kind(cell) if isinstance(cell, str) else None
            if kind and kind not in found:
                found[kind] = j
        if "cth" in found:
            header_at, cols = i, found
            break
    if header_at is None:
        raise TariffError('No CTH column found. The first sheet needs a header such as "CTH" '
                          'above the 8-digit codes.')

    out = ParsedFile(columns=[k for k in DATA_FIELDS if k in cols])
    for n, row in enumerate(raw_rows[header_at + 1:], start=header_at + 2):
        raw = row[cols["cth"]] if cols["cth"] < len(row) else None
        if raw is None or str(raw).strip() == "":
            continue
        code = clean_cth(raw)
        if not code:
            out.skipped.append({"row": n, "value": str(raw)[:40], "reason": "not an 8-digit CTH"})
            continue
        data = {k: _cell(row[cols[k]]) if k in cols and cols[k] < len(row) else None for k in DATA_FIELDS}
        if code in out.rows:
            if out.rows[code] != data:
                out.skipped.append({"row": n, "value": code, "reason": "repeated with different values - the first row is kept"})
            continue
        out.rows[code] = data
    if len(out.rows) < MIN_CODES:
        raise TariffError(f"Only {len(out.rows)} valid CTH code(s) found. The full Indian Customs "
                          f"Tariff has about 12,700 - upload the whole list, not a part of it.")
    return out


def _diff(db: Session, parsed: ParsedFile) -> dict:
    existing = {t.cth: t for t in db.query(CustomsTariff).all()}
    added, changed, reactivated, examples_changed = [], [], [], []
    for code, data in parsed.rows.items():
        cur = existing.get(code)
        if cur is None:
            added.append(code)
            continue
        if not cur.is_active:
            reactivated.append(code)
        # Only the columns the file carries: a file without descriptions does not wipe them.
        diffs = {k: (getattr(cur, k), data[k]) for k in parsed.columns if getattr(cur, k) != data[k]}
        if diffs:
            changed.append(code)
            if len(examples_changed) < 10:
                examples_changed.append({"cth": code, "changes": {k: {"from": a, "to": b} for k, (a, b) in diffs.items()}})
    deactivated = sorted(c for c, t in existing.items() if t.is_active and c not in parsed.rows)
    active_now = sum(1 for t in existing.values() if t.is_active)
    return {
        "existing": existing, "added": added, "changed": changed, "reactivated": reactivated,
        "deactivated": deactivated, "active_now": active_now, "examples_changed": examples_changed,
    }


def _summary(parsed: ParsedFile, d: dict) -> dict:
    return {
        "total_codes": len(parsed.rows),
        "columns": ["cth", *parsed.columns],
        "added": len(d["added"]),
        "changed": len(d["changed"]),
        "reactivated": len(d["reactivated"]),
        "deactivated": len(d["deactivated"]),
        "active_now": d["active_now"],
        "examples_added": d["added"][:10],
        "examples_deactivated": d["deactivated"][:10],
        "examples_changed": d["examples_changed"],
        "skipped_rows": len(parsed.skipped),
        "examples_skipped": parsed.skipped[:10],
        # Losing more than a tenth of the live list in one upload is almost always the wrong file.
        "large_removal": d["active_now"] > 0 and len(d["deactivated"]) > d["active_now"] * 0.10,
    }


def preview_upload(db: Session, parsed: ParsedFile) -> dict:
    """What apply_upload would do - nothing is written."""
    return _summary(parsed, _diff(db, parsed))


def apply_upload(db: Session, parsed: ParsedFile, file_name: str, uploaded_by: str | None) -> dict:
    """Replace the master with the file, in one transaction. Commits."""
    d = _diff(db, parsed)
    summary = _summary(parsed, d)
    upload = CustomsTariffUpload(
        file_name=(file_name or "tariff")[:255], uploaded_by=(uploaded_by or "")[:255] or None,
        total_codes=summary["total_codes"], added=summary["added"], changed=summary["changed"],
        deactivated=summary["deactivated"], reactivated=summary["reactivated"],
        skipped_rows=summary["skipped_rows"],
    )
    db.add(upload)
    db.flush()
    now = datetime.now(timezone.utc)
    existing = d["existing"]
    changed, reactivated = set(d["changed"]), set(d["reactivated"])
    for code, data in parsed.rows.items():
        cur = existing.get(code)
        if cur is None:
            db.add(CustomsTariff(cth=code, is_active=True, upload_id=upload.id, **data))
            continue
        if code in changed:
            for k in parsed.columns:
                setattr(cur, k, data[k])
            cur.upload_id = upload.id
        if code in reactivated:
            cur.is_active = True
            cur.deactivated_at = None
            cur.upload_id = upload.id
    for code in d["deactivated"]:
        existing[code].is_active = False
        existing[code].deactivated_at = now
    db.commit()
    _ACTIVE.clear()
    return {**summary, "upload_id": upload.id}


# ---------------------------------------------------------------- reading

# {"key": latest upload id, "codes": frozenset} - the set every check uses, rebuilt only when a
# new upload exists (each worker process notices on its own via the latest upload id).
_ACTIVE: dict = {}


def _latest_upload_id(db: Session) -> str | None:
    row = db.query(CustomsTariffUpload.id).order_by(CustomsTariffUpload.created_at.desc()).first()
    return row[0] if row else None


def active_codes(db: Session) -> frozenset[str]:
    """Every active 8-digit CTH. Empty when no tariff has been uploaded yet."""
    key = _latest_upload_id(db)
    if _ACTIVE.get("key") != key or "codes" not in _ACTIVE:
        codes = frozenset(c for (c,) in db.query(CustomsTariff.cth).filter(CustomsTariff.is_active.is_(True)))
        _ACTIVE.clear()
        _ACTIVE.update(key=key, codes=codes)
    return _ACTIVE["codes"]


def is_loaded(db: Session) -> bool:
    return bool(active_codes(db))


def is_allowed(db: Session, raw) -> bool:
    """True when this value is an active code of the master."""
    code = clean_cth(raw)
    return bool(code) and code in active_codes(db)


def get_code(db: Session, raw) -> CustomsTariff | None:
    code = clean_cth(raw)
    if not code:
        return None
    return db.query(CustomsTariff).filter(CustomsTariff.cth == code).first()


def row_out(t: CustomsTariff) -> dict:
    return {
        "cth": t.cth, "description": t.description, "rate": t.rate, "unit": t.unit,
        "preferential_rate": t.preferential_rate, "is_active": t.is_active,
        "deactivated_at": t.deactivated_at.isoformat() if t.deactivated_at else None,
    }


def search(db: Session, q: str = "", include_inactive: bool = False, page: int = 1,
           page_size: int = 25) -> dict:
    """One page of the master. Digits search the code from its start ("8517" -> every code
    under heading 8517); words search the description, unit and rate."""
    query = db.query(CustomsTariff)
    if not include_inactive:
        query = query.filter(CustomsTariff.is_active.is_(True))
    term = " ".join((q or "").split())
    digits = re.sub(r"[\s.\-]", "", term)
    if digits.isdigit():
        query = query.filter(CustomsTariff.cth.like(f"{digits[:CTH_LENGTH]}%"))
    elif term:
        for word in term.lower().split()[:6]:
            like = f"%{word}%"
            query = query.filter(or_(
                func.lower(func.coalesce(CustomsTariff.description, "")).like(like),
                func.lower(func.coalesce(CustomsTariff.unit, "")).like(like),
                func.lower(func.coalesce(CustomsTariff.rate, "")).like(like),
            ))
    total = query.count()
    rows = query.order_by(CustomsTariff.cth).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "rows": [row_out(t) for t in rows]}


def summary(db: Session) -> dict:
    active = db.query(func.count(CustomsTariff.id)).filter(CustomsTariff.is_active.is_(True)).scalar() or 0
    inactive = db.query(func.count(CustomsTariff.id)).filter(CustomsTariff.is_active.is_(False)).scalar() or 0
    with_desc = db.query(func.count(CustomsTariff.id)).filter(
        CustomsTariff.is_active.is_(True), CustomsTariff.description.isnot(None)).scalar() or 0
    last = db.query(CustomsTariffUpload).order_by(CustomsTariffUpload.created_at.desc()).first()
    return {
        "active": active, "inactive": inactive, "with_description": with_desc,
        "last_upload": upload_out(last) if last else None,
    }


def upload_out(u: CustomsTariffUpload) -> dict:
    return {
        "id": u.id, "file_name": u.file_name, "uploaded_by": u.uploaded_by,
        "uploaded_at": u.created_at.isoformat() if u.created_at else None,
        "total_codes": u.total_codes, "added": u.added, "changed": u.changed,
        "deactivated": u.deactivated, "reactivated": u.reactivated, "skipped_rows": u.skipped_rows,
    }
