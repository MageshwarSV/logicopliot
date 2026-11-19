"""Excel by sheet: what happens to the Excel files of a job whose template has
excel_mode = "sheet" (TemplateGroup.excel_mode). Shared by the email puller and Smart Upload.

For each workbook:
  * on the template's ignore list (internal tracking sheets like DSRImportTemplate) -> every
    sheet to Unclassified, no AI call;
  * otherwise each visible, non-empty sheet is classified on its own from its cells (the mode
    classifier's text reader - never vision; a sheet has no page image and its text is exact):
      - a confident slot -> placed in that slot as an Excel document (read from its cells at
        extraction), unless the same invoice / packing list number is already in that slot as
        a PDF - then the PDF is kept and the sheet goes to Unclassified ("PDF version used");
      - no confident slot -> Unclassified.
  Everything left in Unclassified can still be assigned by an operator.

Excel sheets are classified SEPARATELY from the job's PDFs and after them: the PDF classifier
weighs every file of a mail against the others, and adding sheets to that would change where
PDFs land. This module never moves a PDF.

Billing (agreed 2026-11-19): every non-empty sheet read is billed at intake, 20 rows = 1 page,
minimum 1 - placed or Unclassified alike; an ignore-list sheet counts 1 page; empty sheets free.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.core.config import get_settings
from app.core.excel_text import (EXCEL_EXTS, Sheet, WorkbookLocked, WorkbookUnreadable,
                                 read_workbook, sheet_text)

logger = logging.getLogger(__name__)

DEFAULT_IGNORE_PATTERNS = ["DSRImportTemplate", "Consol Planning Sheet", "PDFSummary", "ShipNotice"]
CLASSIFY_PREVIEW_ROWS = 60

REASON_IGNORED = "on the ignore list"
REASON_NOT_A_DOCUMENT = "not recognised as a customs document"


def is_sheet_mode(group) -> bool:
    return group is not None and (getattr(group, "excel_mode", None) or "legacy") == "sheet"


def is_excel(name: str) -> bool:
    return PurePosixPath(name or "").suffix.lower() in EXCEL_EXTS


def ignore_patterns(group) -> list[str]:
    pats = getattr(group, "excel_ignore_patterns", None)
    return list(DEFAULT_IGNORE_PATTERNS if pats is None else pats)


def is_ignored(filename: str, patterns: list[str]) -> bool:
    low = (filename or "").lower()
    return any(p.strip() and p.strip().lower() in low for p in patterns)


def unclassified_excel_dir(job_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "jobs" / job_id / "unclassified_excel"


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", PurePosixPath(name or "file.xlsx").name).strip() or "file.xlsx"
    return stem[-120:]


# ---- same invoice already as a PDF? -----------------------------------------------------------

_NUMBER_LABEL = re.compile(
    r"^(commercial\s+)?(invoice|inv|packing\s*list|pl|proforma)\s*(no\.?|number|num|#)\s*:?$", re.I)


def document_number(sheet: Sheet) -> str | None:
    """The invoice / packing list number printed on the sheet: the cell after (or below) a label
    like "Invoice No". None when there is no such label - then no duplicate check is made."""
    by_row = {r: dict(cells) for r, cells in sheet.rows}
    for r, cells in sheet.rows:
        for i, (col, text) in enumerate(cells):
            if not _NUMBER_LABEL.match(text.strip()):
                continue
            if i + 1 < len(cells) and cells[i + 1][1].strip():
                return cells[i + 1][1].strip()
            below = by_row.get(r + 1, {}).get(col)
            if below:
                return below.strip()
    return None


def _key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def same_as_pdf(number: str | None, pdf_texts: list[str]) -> bool:
    k = _key(number or "")
    if len(k) < 4:                     # too short to compare safely
        return False
    return any(k in _key(t) for t in pdf_texts)


# ---- intake ----------------------------------------------------------------------------------

@dataclass
class ExcelIntake:
    filled: list[str] = field(default_factory=list)          # slot names that got an Excel sheet
    placed: list[str] = field(default_factory=list)          # "file - sheet -> slot" log lines
    unclassified_sheets: int = 0
    billable_pages: int = 0
    skipped: list[dict] = field(default_factory=list)        # for Job.attachment_notes
    locked: list[dict] = field(default_factory=list)


def intake_excel_files(db, job, group, files: list[tuple[str, bytes]], cand_slots: list[dict],
                       slots: dict, placed_pdf_texts: dict[str, list[str]],
                       *, is_rerun: bool = False, blocked_keys: set[str] | None = None) -> ExcelIntake:
    """Place or park each sheet of `files` (Excel only) on `job`. `cand_slots` = the classifier's
    candidate slots ({"key", "name", "doc_type", ...}, as the PDF classifier got them); `slots` =
    {template_document_id: JobDocument} (updated in place when a slot gets another file);
    `placed_pdf_texts` = {template_document_id: [text of each PDF placed there]}.
    Writes rows and files; does not commit."""
    from app.api.v1.jobs import _job_doc_dir
    from app.core.billing import record_billable_pages
    from app.core.classifier_common import classifier_module
    from app.models.job import JobDocument, UnclassifiedFile

    out = ExcelIntake()
    patterns = ignore_patterns(group)
    meta = {c["key"]: c for c in cand_slots}
    classify = classifier_module(group.mode).classify_document

    for name, data in files:
        try:
            wb = read_workbook(data, name)
        except WorkbookLocked:
            out.locked.append({"path": name, "files": [name]})
            continue
        except WorkbookUnreadable:
            out.skipped.append({"path": name, "reason": "Excel file could not be read"})
            continue

        sheets = [s for s in wb.sheets if s.data_rows]
        parked: list[dict] = []
        if is_ignored(name, patterns):
            parked = [{"name": s.name, "rows": s.data_rows, "reason": REASON_IGNORED} for s in sheets]
            out.billable_pages += len(sheets)                       # 1 page per sheet
        else:
            for sheet in sheets:
                out.billable_pages += sheet.billable_pages
                label = f"{name} - {sheet.name}"
                try:
                    claims = classify(label, sheet_text(sheet, CLASSIFY_PREVIEW_ROWS), None, cand_slots)
                except Exception:  # noqa: BLE001 - one sheet must not lose the rest of the job
                    logger.exception("excel intake: could not classify %s", label[:120])
                    claims = []
                strong = [c for c in claims if c.get("source") != "ai_second" and c.get("key") in slots]
                if not strong:
                    parked.append({"name": sheet.name, "rows": sheet.data_rows, "reason": REASON_NOT_A_DOCUMENT})
                    continue
                key = max(strong, key=lambda c: len(c.get("evidence") or ""))["key"]
                if blocked_keys and key in blocked_keys:
                    # e.g. the Air Waybill of a consolidation comes only from the zip's main folder.
                    parked.append({"name": sheet.name, "rows": sheet.data_rows,
                                   "reason": "Air Waybill is taken only from the main folder"})
                    continue
                number = document_number(sheet)
                if same_as_pdf(number, placed_pdf_texts.get(key, [])):
                    parked.append({"name": sheet.name, "rows": sheet.data_rows,
                                   "reason": f"PDF version used: {number}",
                                   "suggested_key": key})
                    continue
                jd = slots[key]
                if jd.file_path:
                    jd = JobDocument(tenant_id=job.tenant_id, job_id=job.id, template_document_id=key,
                                     page_count=0, file_index=jd.file_index + 1)
                    db.add(jd)
                    db.flush()
                    slots[key] = jd
                ddir = _job_doc_dir(jd.id)
                ddir.mkdir(parents=True, exist_ok=True)
                dest = ddir / _safe_name(name)
                dest.write_bytes(data)
                jd.file_path = str(dest)
                jd.source_kind = "excel"
                jd.sheet_name = sheet.name
                jd.page_count = 0
                jd.original_name = label[:255]
                slot_name = (meta.get(key) or {}).get("name") or key
                if slot_name not in out.filled:
                    out.filled.append(slot_name)
                out.placed.append(f"{label} -> {slot_name}")
                logger.info("excel intake: %s -> %s", label[:120], slot_name)

        if parked:
            udir = unclassified_excel_dir(job.id)
            udir.mkdir(parents=True, exist_ok=True)
            dest = udir / f"{uuid.uuid4().hex[:8]}_{_safe_name(name)}"
            dest.write_bytes(data)
            db.add(UnclassifiedFile(tenant_id=job.tenant_id, job_id=job.id,
                                    original_filename=name[:255], file_path=str(dest),
                                    sheets=parked, reason=parked[0]["reason"][:255]))
            out.unclassified_sheets += len(parked)

    if out.billable_pages:
        record_billable_pages(db, job, out.billable_pages, is_rerun=is_rerun)
    return out
