import hashlib
import io
import json
import logging
import math
import re
import shutil
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
# The function, not the module: `def job_history(...)` in this file shadows a module of
# that name at module level, so `job_history.describe` resolved to the ROUTE and raised
# AttributeError on every request. Legal Python - it imported cleanly and 500'd live.
from app.db.job_history import describe as describe_status
from app.core.deps import (
    TenantScope,
    get_current_user,
    get_db,
    get_tenant_scope,
    require_role,
    require_write_access,
    scoped_query,
)
from app.core.classifier import AIServiceUnavailable, assign_documents_detailed
from app.core.docai import get_page_ocr, locate_value_bbox, ocr_page_image
from app.core.page_filter import extract_pdf_pages, kept_page_to_original
from app.core.custom_page_filter import filter_pages_with_custom, get_active_custom_filter_texts
from app.core.system_settings import is_extraction_paused
from app.core.extraction import (
    compare_values,
    extract_document_fields,
    field_spec,
    extract_document_fields_from_images,
    extract_document_rows,
    extract_document_rows_from_images,
    is_party_field,
    numeric_total,
)
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.core import live_runs
from app.models.job_event import JobEvent
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.jobs import (
    AvailableGroup,
    DuplicateDecision,
    FieldValueCorrect,
    JobCreate,
    JobDetailOut,
    JobDocumentOut,
    JobEventOut,
    JobFieldValueOut,
    JobOut,
    VerificationDecision,
    VerificationRow,
)

logger = logging.getLogger(__name__)

def _column_fill(cfg: dict, values: dict, rows: dict | None) -> tuple[int, int]:
    """(columns that would carry a value, columns mapped at all) for this job.

    A thin workbook and a full one look identical in the log otherwise, and a template whose
    fields stopped extracting is exactly the case worth noticing.
    """
    try:
        from app.core.excel_entry import SERIALS, sheet_plans
    except Exception:  # noqa: BLE001 — diagnostics must never break a run
        return (1, 1)          # unknown: never let a diagnostic refuse a run
    rows = rows or {}
    filled = mapped = 0
    for plan in sheet_plans(cfg):
        for col in plan.get("columns") or []:
            field = (col.get("field") or "").strip()
            if not field or field in SERIALS:
                continue
            mapped += 1
            if rows.get(field) or str(values.get(field) or "").strip() != "":
                filled += 1
    return (filled, mapped)


def _unfilled_columns(cfg: dict, values: dict, rows: dict | None) -> int:
    """How many mapped columns would come out blank. Kept for the log line."""
    filled, mapped = _column_fill(cfg, values, rows)
    return mapped - filled


router = APIRouter(tags=["jobs"])

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_FILE_BYTES = 25 * 1024 * 1024
RENDER_DPI = 150

# Stage shown while a job is waiting on the operator to answer the ruling's question
# (e.g. the incoterm could not be found in any document). Stored in Job.stage_override,
# which _job_stage already honours ahead of the computed stage — so no schema change.
HOLD_STAGE = "Hold"

# Character budget for the document text handed to the ruling, split evenly between the
# uploaded documents so every one is represented rather than the largest swallowing it all.
# Characters, not tokens: ~4 chars per token, so 40k is ~10k tokens against a 128k context.
RULING_TEXT_BUDGET = 40000

# Verification statuses that require a human before the entry may be submitted. "missing" is
# NOT one of them: a field is missing when the other document simply does not carry it — no BL
# number on an invoice, no invoice number on a freight certificate — or when that document was
# not part of this shipment at all. Those never resolve, so blocking on them meant the operator
# accepted the same warnings on every job, which is how real warnings stop being read. They are
# still shown on the verification screen; they just do not hold the job.
BLOCKING_VERIFICATION_STATUSES = {"mismatch", "review"}

# The closed set of Incoterms, matched on word boundaries. Payment terms such as
# "FREIGHT PREPAID" / "FREIGHT COLLECT" are deliberately absent — they are not Incoterms.
INCOTERM_RE = re.compile(
    r"\b(EXWORKS?|EX-?WORKS?|EXW|FCA|FOB|FAS|CIF|CFR|C&F|CPT|CIP|DAP|DPU|DDP)\b",
    re.IGNORECASE,
)


def _job_doc_dir(job_document_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "jobs" / job_document_id


def generate_job_no() -> str:
    """A short random, human-readable job number, e.g. JOB-7F3A9C."""
    import uuid

    return "JOB-" + uuid.uuid4().hex[:6].upper()


def _render_pages(original: Path, pages_dir: Path, skip: list[int] | None = None) -> int:
    """Render a document's pages for the operator to look at. Returns how many were kept.

    `skip` names pages of the ORIGINAL that should not be rendered - in practice the carrier's
    terms and conditions on the back of a sea waybill. A 2-page bill of lading whose second page
    is 39,000 characters of legal boilerplate is a 1-page document as far as anyone using this
    is concerned, and showing the terms page only invites someone to look for data on it.

    Kept pages are renumbered from 1 so the viewer has no gaps. The original file is untouched,
    so nothing is actually lost - a dropped page can always be recovered from it.
    """
    import fitz

    pages_dir.mkdir(parents=True, exist_ok=True)
    drop = set(skip or [])
    kept = 0
    with fitz.open(original) as doc:
        for index, page in enumerate(doc, start=1):
            if index in drop:
                continue
            kept += 1
            page.get_pixmap(dpi=RENDER_DPI).save(pages_dir / f"page_{kept}.png")
    # A previous render of the same document may have left higher-numbered pages behind.
    for leftover in pages_dir.glob("page_*.png"):
        try:
            if int(leftover.stem.split("_")[-1]) > kept:
                leftover.unlink()
        except ValueError:
            continue
    return kept


def _load_job(db: Session, job_id: str, scope: TenantScope, user: User | None = None) -> Job:
    job = scoped_query(db, Job, scope).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    # An operator may not touch another operator's mail-routed job - UNLESS they are mode-
    # assigned (Sea Import, Sea Export, ...), in which case mode replaces ownership entirely
    # as their access gate, same as list_jobs above and the same shape as GK2's check just
    # below: list_jobs filtering by mode alone only hid a job from the LIST, it never actually
    # stopped opening or acting on one by a known/guessed id, which is the gap this closes.
    if user is not None and user.role == OPERATOR:
        if user.assigned_modes:
            group = db.get(TemplateGroup, job.group_id)
            if group is None or group.mode not in user.assigned_modes:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        elif job.assigned_operator_id not in (None, user.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    # A GK2 user's assigned_modes is the REAL access gate (see User.assigned_modes) - every
    # job endpoint goes through here, so this is the one place that has to enforce it.
    # list_jobs filtering by it alone only hid a restricted job from the LIST; it never
    # actually stopped opening or acting on one by a known/guessed id, which is the gap this
    # closes - same 404-not-found shape as the operator check above.
    if user is not None and user.role == GK2:
        group = db.get(TemplateGroup, job.group_id)
        if group is None or group.mode not in (user.assigned_modes or []):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


# Statuses that describe where a job ENDED UP, as opposed to where it is waiting. A stage
# override says "this job is parked on such-and-such a screen", which stops being true the
# moment the job reaches one of these - so these win over it.
_SETTLED = {"processing", "completed", "failed", "duplicate"}

# The stages an operator works through, in order. The words the job list, the job header and
# the history all use — one list, so they cannot drift apart. The backend can only OBSERVE
# three of them (capture, validation, and the end); the ones between are known from what the
# operator recorded moving to.
_OPERATOR_STAGES = (
    "Document Capture",
    "Data Extraction",
    "Data Validation",
    "Dump Data",
    "Manual Data Entry",
    "IRN Processing",
    "ERP Submission",
)


def _fmt_total(n: float) -> str:
    return f"{n:.3f}".rstrip("0").rstrip(".")


def _compare_rows(src: list, tgt: list, *, party: bool = False) -> tuple[str, str, str]:
    """Row-by-row comparison of two line-item columns.

    Returns (status, source_summary, target_summary). Any differing row makes the whole
    check a mismatch, and a differing row COUNT is itself a mismatch — a packing list
    listing fewer items than the invoice is exactly what this is meant to catch.

    UNLESS the column is plain numbers (a weight, a piece count, a quantity) - a bill of
    lading's one aggregate gross weight legitimately has fewer "rows" than a weight list's
    per-carton breakdown, and demanding equal counts there reported a false mismatch on
    every such job. For a numeric column, an unequal count instead compares the TOTAL each
    side implies, and only disagrees if the totals themselves don't add up. A column of
    text (descriptions, part codes) has no sensible total, so it keeps the strict rule.

    `party` is passed straight through to compare_values - see there for what it changes.
    """
    if not src or not tgt:
        return "missing", f"{len(src)} rows" if src else None, f"{len(tgt)} rows" if tgt else None
    if len(src) != len(tgt):
        src_total, tgt_total = numeric_total(src), numeric_total(tgt)
        if src_total is not None and tgt_total is not None:
            st = "match" if math.isclose(src_total, tgt_total, rel_tol=0.02, abs_tol=0.5) else "mismatch"
            return (st, f"{len(src)} rows, total {_fmt_total(src_total)}",
                    f"{len(tgt)} rows, total {_fmt_total(tgt_total)}")
        return "mismatch", f"{len(src)} rows", f"{len(tgt)} rows"
    worst = "match"
    first_bad = None
    for i, (a, b) in enumerate(zip(src, tgt), start=1):
        st = compare_values(a, b, party=party)
        if st == "match":
            continue
        if first_bad is None:
            first_bad = (i, a, b)
        worst = "mismatch" if st == "mismatch" else ("review" if worst == "match" else worst)
    if worst == "match":
        return "match", f"{len(src)} rows, all matched", f"{len(tgt)} rows, all matched"
    i, a, b = first_bad
    return worst, f"row {i}: {a}", f"row {i}: {b}"


_STATUS_RANK = {"match": 0, "review": 1, "mismatch": 2, "missing": 3}


def _best_cross_compare(s_candidates: list, t_candidates: list, *,
                        party: bool = False) -> tuple[str, str | None, str | None]:
    """Compare every (source, target) pair and keep the best result.

    Needed because a "whole job" document (set_index is None — a bill of lading with no
    invoice number of its own to pair on) can carry MORE THAN ONE real value for the same
    mark when there is more than one such file on the job — three separate bills of lading,
    one per invoice, none of which name an invoice number the pairing in doc_sets.py could
    have used. Comparing only the FIRST of those values against every invoice set (the old
    behaviour) reported a false mismatch whenever the invoice actually matched a LATER file,
    and a false "missing" whenever the first file's own value was blank while a later one
    held the real one — exactly "the document has the value, it just didn't get compared".
    Trying every pair finds a real match wherever one genuinely exists, and only falls back to
    mismatch/missing when NONE of the candidates agree.

    `party` is passed straight through to compare_values - see there for what it changes.
    """
    best: tuple[str, str | None, str | None] | None = None
    for sv in s_candidates:
        for tv in t_candidates:
            st = compare_values(sv, tv, party=party)
            if best is None or _STATUS_RANK[st] < _STATUS_RANK[best[0]]:
                best = (st, sv, tv)
                if st == "match":
                    return best
    return best or ("missing", None, None)


def _best_rows_compare(s_candidates: list[list], t_candidates: list[list], *,
                       party: bool = False) -> tuple[str, str, str]:
    """_best_cross_compare's counterpart for a per-row column: a whole-job document with more
    than one file (three separate weight lists, none tied to one invoice set) contributes one
    row-list PER FILE rather than one collapsed list. Try every source-file/target-file
    pairing through _compare_rows and keep the best result, instead of only ever comparing
    against whichever file's rows the database happened to return first."""
    if not s_candidates and not t_candidates:
        return "missing", None, None
    best: tuple[str, str, str] | None = None
    for s_rows in (s_candidates or [[]]):
        for t_rows in (t_candidates or [[]]):
            st, sv, tv = _compare_rows(s_rows, t_rows, party=party)
            if best is None or _STATUS_RANK[st] < _STATUS_RANK[best[0]]:
                best = (st, sv, tv)
                if st == "match":
                    return best
    return best


def _best_rows_to_scalar_compare(
    row_candidates: list[list], scalar_candidates: list, *, party: bool = False,
) -> tuple[str, str | None, str | None]:
    """One side of the link is a per-row numeric column (an Invoice's quantity, one value per
    product line); the other is a single job-level value with no rows of its own at all (a
    Packing List's one aggregate total_quantity, never split per line on that document).

    Neither _best_rows_compare (needs rows on both sides) nor _best_cross_compare (needs a
    single value on both sides) fits this shape - the old routing treated the scalar side as
    "zero rows" and reported "missing" without ever looking at the real value sitting right
    there, which is exactly the reported bug: a packing list's total_quantity against an
    invoice's own per-line quantities came back unverified instead of checked.

    Same rule _compare_rows already uses for two row lists of UNEQUAL length - sum whichever
    side has rows (via numeric_total, so a non-numeric column such as descriptions correctly
    refuses to produce a total) and compare that sum against the other side's own value with
    the same numeric tolerance. A non-numeric row column, or a scalar that isn't a number
    either, has no sensible total to compare - "missing", not a guessed match or mismatch.
    """
    if not row_candidates or not scalar_candidates:
        return "missing", None, None
    best: tuple[str, str | None, str | None] | None = None
    for rows in row_candidates:
        row_total = numeric_total(rows)
        for scalar in scalar_candidates:
            scalar_total = numeric_total([scalar]) if scalar is not None else None
            if row_total is None or scalar_total is None:
                st, sv, tv = "missing", (f"{len(rows)} rows" if rows else None), scalar
            else:
                st = "match" if math.isclose(row_total, scalar_total, rel_tol=0.02, abs_tol=0.5) else "mismatch"
                sv, tv = f"{len(rows)} rows, total {_fmt_total(row_total)}", scalar
            if best is None or _STATUS_RANK[st] < _STATUS_RANK[best[0]]:
                best = (st, sv, tv)
                if st == "match":
                    return best
    return best or ("missing", None, None)


def _numeric_total_field(single_values: dict, row_label: str) -> float | None:
    """This document's own printed TOTAL for a line-item column, when the template also
    captures one as an ordinary single-value field alongside the row-level one - e.g.
    "total_quantity" next to "item_quantity". None when no such field exists on this
    document, or its value isn't numeric - the row-count decision this backs then falls
    back to the plain "more rows" preference, same as before this existed.
    """
    bare = re.sub(r"^(item|products?|product)_", "", row_label, flags=re.IGNORECASE)
    for candidate in (f"total_{bare}", f"{bare}_total"):
        for key, value in single_values.items():
            if key.lower() == candidate.lower():
                total = numeric_total([value])
                if total is not None:
                    return total
    return None


def _row_sums_disagree(rows_a: list, rows_b: list, labels: list[str]) -> bool:
    """Two independent reads of the SAME table (OCR text vs the page image) can agree on the
    row COUNT while still disagreeing on individual VALUES - a value dropped from the middle
    of the table shifts everything after it, and a value borrowed from elsewhere on the page
    (the table's own grand total, a neighbouring row) can fill the resulting gap and still
    produce the RIGHT row count by coincidence. run_extraction's existing text-vs-vision
    cross-check only ever compares row COUNTS (see _row_read_looks_broken and the caller right
    after it) - structurally blind to exactly this failure, found live: an invoice's own
    item_quantity read 7 rows both ways, the count no one had reason to doubt, while one row's
    real value had been dropped and a dollar total misread in its place.

    Layout-independent by construction: it never asks what the table LOOKS like, only whether
    the two reads' own numeric columns add up to the same thing - the same arithmetic check
    _best_rows_to_scalar_compare already uses to verify a job's documents against EACH OTHER,
    applied here to verify one document's two READING METHODS against each other before either
    is trusted. A column that isn't numeric on both sides (descriptions, part codes) has
    nothing to reconcile and is silently skipped, not treated as evidence either way.
    """
    for label in labels:
        total_a = numeric_total([r.get(label) for r in rows_a])
        total_b = numeric_total([r.get(label) for r in rows_b])
        if total_a is None or total_b is None:
            continue
        if not math.isclose(total_a, total_b, rel_tol=0.02, abs_tol=0.5):
            return True
    return False


def _link_source_key(link: CrossDocLink) -> str:
    """A CrossDocLink's source id, whichever side is actually set - a mark or a custom
    field, never both. See CrossDocLink.source_custom_field_id."""
    return link.source_mark_id or link.source_custom_field_id


def _verification_findings(db: Session, job: Job, links: list | None = None) -> list[dict]:
    """Every cross-document check on this job — one per (link, set).

    Verification runs SET BY SET: invoice 2 is checked against its own packing list, the one
    carrying the same invoice number, never the one that happened to be uploaded second. The
    pairing is done in run_extraction (see app/core/doc_sets.py) and recorded on set_index.

    Comparing across sets is the failure that matters here, because it does not look like a
    failure: it reports a confident tick between two documents that were never meant to agree.

    A document that belongs to no single set — one bill of lading covering all three invoices —
    is checked against EVERY set, since it genuinely does describe all of them.

    ONE implementation, shared by the stage gate and the verification screen. They were two
    near-identical copies with a comment insisting they must agree, which is how they drifted.
    """
    links = links if links is not None else db.query(CrossDocLink).filter(
        CrossDocLink.group_id == job.group_id).all()
    if not links:
        return []

    # Whether each link's OWN field is a company name/address (Consignee, Exporter, Supplier
    # Name/Address, ...) rather than a description/dosage/part code - see
    # compare_values(party=...) for why that changes how close a "close enough" text match
    # has to be. Looked up once per link rather than per set, and by either side's label so a
    # link between two documents naming the same party differently still gets it.
    #
    # A link's SOURCE is a mark or a custom field, never both - see
    # CrossDocLink.source_custom_field_id. _link_source_key returns whichever id is set, and
    # every dict below (mark_labels, vals, wide_vals, rws, wide_rows) is keyed on that same id
    # regardless of which table it actually names, so the rest of this function never needs to
    # know which kind a given key is.
    mark_ids = {link.target_mark_id for link in links}
    custom_field_ids = set()
    for link in links:
        if link.source_mark_id is not None:
            mark_ids.add(link.source_mark_id)
        else:
            custom_field_ids.add(link.source_custom_field_id)
    mark_labels = {
        m.id: m.label_name
        for m in db.query(FieldMark).filter(FieldMark.id.in_(mark_ids)).all()
    } if mark_ids else {}
    if custom_field_ids:
        from app.models.custom_field import CustomField

        mark_labels.update({
            c.id: c.label_name
            for c in db.query(CustomField).filter(CustomField.id.in_(custom_field_ids)).all()
        })
    party_by_link = {
        link.id: (is_party_field(mark_labels.get(_link_source_key(link)))
                 or is_party_field(mark_labels.get(link.target_mark_id)))
        for link in links
    }

    fvs = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    vals: dict[tuple, str] = {}          # (set, mark) -> job-level value
    rws: dict = {}                       # (set, mark) -> {row number: value}, then a list
    # mark -> EVERY value seen on a whole-job document, not just the first — see
    # _best_cross_compare for why more than one can be real (several bills of lading, one per
    # invoice, none carrying an invoice number of their own to be given a real set_index).
    wide_vals: dict[str, list[str | None]] = {}
    # (mark, job_document_id) -> {row number: value} — kept apart PER FILE rather than
    # collapsed into one dict keyed on mark alone. A whole-job document (set_index None) can
    # have more than one file - three separate weight lists, none carrying an invoice number
    # to be paired on - and each file's OWN row numbering restarts at row 1. Collapsing them
    # into one bucket by row number kept only whichever file's row 1 the database happened
    # to return first and threw the rest away, the exact row-level version of the
    # single-value wide_vals bug above. Grouping by file first and turning each file's rows
    # into its own candidate list (below) fixes that while still never blindly appending two
    # files' rows into one inflated list — a genuine accidental re-upload of the same file
    # just becomes two identical candidates, which compares the same either way.
    wide_rows_by_file: dict[tuple[str, str | None], dict] = {}
    for fv in fvs:
        # A custom-field-sourced link needs that field's OWN values here too - previously
        # only fv.mark_id was ever a real link endpoint, so a custom field's JobFieldValue
        # rows were skipped outright. Sharing one key space between mark ids and custom
        # field ids is safe: only a key an actual link names ever gets looked up below, and
        # the two id spaces don't collide (different tables, both UUIDs).
        key = fv.mark_id or fv.custom_field_id
        if key is None:
            continue
        if fv.row_index is None:
            if fv.set_index is None:
                wide_vals.setdefault(key, []).append(fv.value)
            else:
                vals[(fv.set_index, key)] = fv.value
        else:
            if fv.set_index is None:
                slot = wide_rows_by_file.setdefault((key, fv.job_document_id), {})
            else:
                slot = rws.setdefault((fv.set_index, key), {})
            if not (slot.get(fv.row_index) or "").strip():
                slot[fv.row_index] = fv.value or ""
    rws = {k: [v for _, v in sorted(d.items())] for k, d in rws.items()}
    wide_rows: dict[str, list[list]] = {}
    for (mark_id, _doc_id), d in wide_rows_by_file.items():
        wide_rows.setdefault(mark_id, []).append([v for _, v in sorted(d.items())])

    sets = sorted({k[0] for k in vals} | {k[0] for k in rws}) or [1]
    accepted = set(job.accepted_verifications or [])
    out: list[dict] = []
    def _scalar_candidates(key: str, s: int) -> list:
        return (
            [vals[(s, key)]] if (s, key) in vals
            else wide_vals.get(key) or [None]
        )

    for s in sets:
        for link in links:
            party = party_by_link[link.id]
            src_key = _link_source_key(link)
            s_row_candidates = (
                [rws[(s, src_key)]] if (s, src_key) in rws
                else wide_rows.get(src_key) or []
            )
            t_row_candidates = (
                [rws[(s, link.target_mark_id)]] if (s, link.target_mark_id) in rws
                else wide_rows.get(link.target_mark_id) or []
            )
            s_has_rows, t_has_rows = bool(s_row_candidates), bool(t_row_candidates)
            if s_has_rows and t_has_rows:
                st, sv, tv = _best_rows_compare(s_row_candidates, t_row_candidates, party=party)
            elif s_has_rows != t_has_rows:
                # Exactly one side is a per-row field (several product lines) - the other is
                # a single job-level value with no rows of its own at all, e.g. a Packing
                # List's one aggregate total_quantity against an Invoice's own item_quantity
                # PER LINE. The old code read the scalar side as "zero rows" and reported
                # missing without ever looking at the real value sitting right there - this
                # sums the row side and compares that sum against the other side's own value,
                # see _best_rows_to_scalar_compare.
                if s_has_rows:
                    st, sv, tv = _best_rows_to_scalar_compare(
                        s_row_candidates, _scalar_candidates(link.target_mark_id, s), party=party)
                else:
                    st, tv, sv = _best_rows_to_scalar_compare(
                        t_row_candidates, _scalar_candidates(src_key, s), party=party)
            else:
                s_candidates = _scalar_candidates(src_key, s)
                t_candidates = _scalar_candidates(link.target_mark_id, s)
                if all(c is None for c in s_candidates) and all(c is None for c in t_candidates):
                    # Neither document in this set carries the field. Nothing to report —
                    # and reporting it would bury the real findings under empty rows once a
                    # job has several sets.
                    continue
                st, sv, tv = _best_cross_compare(s_candidates, t_candidates, party=party)
            # Keyed on the actual compared VALUES, not just the link+set - an acceptance
            # used to be keyed on link identity alone, so a value corrected or recomputed
            # AFTER being accepted (see correct_field_value, recompute_mark,
            # recompute_custom_field - none of them touch accepted_verifications) kept
            # reading as accepted even though the operator never looked at the NEW values,
            # only whatever was there before. A short hash of what is actually being shown
            # makes an old acceptance stop applying the instant either side's value
            # changes, without any of those write paths needing to know this exists.
            content = hashlib.sha256(f"{sv}\x00{tv}".encode()).hexdigest()[:10]
            fid = f"{link.id}#{s}#{content}"
            out.append({
                "id": fid, "link": link, "set_index": s,
                "source_value": sv, "target_value": tv,
                "status": st, "accepted": fid in accepted,
            })
    return out


_VERDICT_STATUS_RE = re.compile(r"^\s*(MATCH|MISMATCH|CANNOT VERIFY)\b")


def _self_verifying_findings(db: Session, job: Job) -> list[dict]:
    """Some custom fields ARE their own cross-document check: their AI prompt already reads
    several documents and reconciles them itself, returning a verdict in the shape "MATCH -
    ...", "MISMATCH - ..." or "CANNOT VERIFY - ..." (e.g. Air Import's "Quantity
    Verification", "Amount Verification"). A plain computed total worth showing alongside
    them for context (a "... (Calculated)" field, e.g. "Total Amount (Calculated)") rides
    along too, as an informational row that never blocks.

    Neither kind has a target mark to link against through CrossDocLink - that model is for
    a field checked against ONE other document's mark (see source_custom_field_id), not for a
    field that already checked several documents on its own - so these never become
    CrossDocLink rows at all. They are folded into the SAME gate (_job_stage) and the SAME
    Cross Docs Verification screen as mark-to-mark links, so a real MISMATCH here blocks a
    job exactly like one does - detected from the VALUE's own shape, not a hardcoded list of
    field names, so any future field using the same MATCH/MISMATCH convention picks this up
    automatically.

    A field already linked to a mark through a real CrossDocLink is excluded here - it
    already gets a row through _verification_findings, and showing the SAME field a second
    time, unlinked, would just duplicate it. Checked by an actual CrossDocLink row, not the
    verify_with_other_document flag alone - the flag is set by the /link-marks endpoint, but
    the link itself, not the flag, is what _verification_findings actually keys off.
    """
    from app.models.custom_field import CustomField

    fields = (
        db.query(CustomField)
        .filter(CustomField.group_id == job.group_id, CustomField.kind == "ai")
        .all()
    )
    already_linked = {
        link.source_custom_field_id
        for link in db.query(CrossDocLink).filter(
            CrossDocLink.group_id == job.group_id,
            CrossDocLink.source_custom_field_id.isnot(None),
        ).all()
    }
    candidates = [
        cf for cf in fields
        if cf.id not in already_linked
        and ("verification" in cf.label_name.lower()
             or cf.label_name.strip().lower().endswith("(calculated)"))
    ]
    if not candidates:
        return []
    fvs = {
        fv.custom_field_id: fv.value
        for fv in db.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.row_index.is_(None),
            JobFieldValue.custom_field_id.in_([cf.id for cf in candidates]),
        ).all()
    }
    accepted = set(job.accepted_verifications or [])
    out: list[dict] = []
    for cf in candidates:
        value = (fvs.get(cf.id) or "").strip()
        verdict = _VERDICT_STATUS_RE.match(value)
        if verdict:
            word = verdict.group(1)
            status = "match" if word == "MATCH" else "mismatch" if word == "MISMATCH" else "review"
        elif value:
            status = "match"  # an informational figure - never blocks on its own
        else:
            status = "missing"
        fid = f"cf:{cf.id}"
        out.append({
            "id": fid, "custom_field": cf, "value": value or None,
            "status": status, "accepted": fid in accepted,
        })
    return out


def _job_stage(db: Session, job: Job) -> str:
    """Where the job sits in the pipeline, for the operator's list/badge:
    Documents → Verification → ERP Entry → Completed.

    The override used to win over EVERYTHING, including a job that had finished. Putting a job
    back on ERP Entry sets that override, and nothing ever cleared it - so a job could run all
    thirty steps, be marked completed, and still sit in the list as "ERP Entry" while its own
    history popup said "Completed" two lines down. Where a job ENDED is not something an
    override about where it was waiting can contradict.
    """
    if job.status == "processing":
        return "Running"
    if job.status == "possible_duplicate":
        # Not the same thing as "duplicate" below - that one means the ERP itself already had
        # this record. This means a NEW job's extracted data matched an EXISTING job's, before
        # either ever reached the ERP - an operator decision, not an ERP outcome.
        return "Possible Duplicate"
    if job.status == "duplicate":
        return "Duplicate"
    if job.status == "failed":
        return "Failed"
    if job.status == "completed":
        return "Completed"
    if job.stage_override:  # a manual override, for a job that has not settled anywhere yet
        return job.stage_override
    if job.status != "extracted":
        return "Document Capture"
    links = db.query(CrossDocLink).filter(CrossDocLink.group_id == job.group_id).all()
    # The same findings the verification screen shows, computed once in one place — so this
    # gate and that screen can no longer disagree about whether the job may be submitted.
    # `links` empty means no findings at all (nothing to disagree) - that used to skip
    # straight to ERP Submission below without a human ever seeing the extracted data. A
    # template having no cross-document checks is not the same as needing no review.
    for f in _verification_findings(db, job, links):
        if f["accepted"]:
            continue
        if f["status"] in BLOCKING_VERIFICATION_STATUSES:
            return "Data Validation"  # something genuinely disagrees — a human must approve
    for f in _self_verifying_findings(db, job):
        if f["accepted"]:
            continue
        if f["status"] in BLOCKING_VERIFICATION_STATUSES:
            return "Data Validation"  # a field's own reconciliation reported MISMATCH
    # Past validation - or nothing to validate at all. Either way a human must still look at
    # the extracted data at least once before it is ready for ERP entry; WHERE past that -
    # Dump Data, Manual Data Entry, ERP Submission - is something only the operator's own
    # recorded progress can say, so use the last stage they recorded moving to.
    moved = (
        db.query(JobEvent)
        .filter(JobEvent.job_id == job.id, JobEvent.stage.isnot(None))
        .order_by(JobEvent.created_at.desc())
        .first()
    )
    if moved is None:
        # Never been reviewed at all - this is also what a no-cross-check template hits the
        # very first time, instead of skipping straight past review.
        return "Data Validation"
    if moved.stage in _OPERATOR_STAGES:
        return moved.stage
    return "ERP Submission"


def _outer_status(db: Session, job: Job) -> str:
    """The single word shown on the job list and the job header — coarser than the seven-stage
    rail on purpose. The rail's words are screens an operator navigates between; this is the
    answer to "where does this job actually stand", for someone who has not opened it.

    Deliberately layered ON TOP of _job_stage() rather than folded into it: the rail, the Next
    button and the green ticks all key off _job_stage()'s exact words, and changing those to
    read better here would change what gates a job's progress too. This function only picks a
    different word for the same computed stage — nothing it returns feeds back into the gate.

    The GK1 (operator) -> GK2 sign-off chain (see Job.gk2_status) is checked first: once an
    operator has submitted a job for approval, THAT is what the badge should say, regardless
    of what _job_stage() would otherwise compute from the (frozen, from here on) rail state.
    """
    if job.gk2_status == "pending":
        return "Pending GK2 Approval"
    if job.gk2_status == "irn_document_process":
        return "IRN Document Process"
    if job.gk2_status == "preparing_erp":
        return "AI - Preparing for ERP"
    if job.gk2_status == "entering_erp":
        return "ERP Entry Process Started"
    if job.gk2_status == "submitted":
        return "AI - ERP Submitted"
    if job.gk2_status == "failed":
        return "Failed"
    if job.status == "extracting":
        return "AI - Processing"
    stage = _job_stage(db, job)
    if stage == "Running":
        return "Submitted"  # the ERP run is in flight — this IS the act of submitting
    if stage == "Document Capture":
        # Whether every MANDATORY document slot actually has a file yet - the same test
        # _maybe_auto_extract() uses to decide when to fire extraction on its own. A job
        # short of one is genuinely waiting on paperwork, not merely "capturing" - that used
        # to read the same as a job one upload away from starting, which is why an operator
        # working a stack of jobs could not tell which ones actually needed something from
        # them versus which were already complete and about to move on their own.
        group = db.get(TemplateGroup, job.group_id)
        required_ids = {td.id for td in (group.documents if group else []) if td.is_required}
        filled_ids = {d.template_document_id for d in job.documents if d.file_path}
        if required_ids.issubset(filled_ids):
            return "Document Capture"
        # A job the email poller created (no operator ever pressed "New Job") is a pre-alert
        # that arrived before its documents were ready to work — a manual smart-upload job in
        # the same stage has no such thing to report, so it keeps the plain word instead.
        return "Pre-Alert Received" if job.created_by_id is None else "Pending Documents"
    if stage == HOLD_STAGE:
        # A ruling hold ("which Incoterm applies?") is a genuinely different thing from
        # waiting on paperwork - it can fire even with every document already in hand. But
        # several of these holds turned out to ALSO be missing a required document (the AI
        # cannot read an Incoterm off a Bill of Lading nobody uploaded yet), and showing
        # "Hold" for those told the operator to go answer a question when the real, more
        # fundamental blocker was still an upload. Missing paperwork wins: only once every
        # required document is actually in does this read as a real ruling question.
        group = db.get(TemplateGroup, job.group_id)
        required_ids = {td.id for td in (group.documents if group else []) if td.is_required}
        filled_ids = {d.template_document_id for d in job.documents if d.file_path}
        if not required_ids.issubset(filled_ids):
            return "Pre-Alert Received" if job.created_by_id is None else "Pending Documents"
        return "Hold"
    if stage == "Completed":
        # A real ERP run finishing is, in the words this feature introduced, the entry
        # having been submitted - the same outcome the GK2 placeholder's "submitted" state
        # names, just reached through the (still dormant) real pipeline instead of the timer.
        return "AI - ERP Submitted"
    if stage in ("Duplicate", "Possible Duplicate", "Failed"):
        return stage
    if stage == "Data Extraction":
        # GK1's whole working phase — Required Details Review AND Cross-Document
        # Verification — reads as one "GK1 Review"/"GK1 Reviewing" pair, the same untouched-
        # vs-already-started distinction Data Validation makes right below. Approving a
        # document, or correcting any looked-up/asked value (Dump Data and Manual Data Entry
        # render on this same tab now), both count as "started".
        acted = any(d.approved or d.gk2_approved for d in job.documents) or (
            db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.corrected_value.isnot(None))
            .first()
            is not None
        )
        return "GK1 Reviewing" if acted else "GK1 Review"
    if stage == "Data Validation":
        # Nothing touched yet vs. the operator has started working it: accepting a flagged
        # mismatch or correcting a value are the two ways to leave a mark before the stage
        # itself moves on, so either one means "in progress" rather than "just arrived".
        acted = bool(job.accepted_verifications) or (
            db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.corrected_value.isnot(None))
            .first()
            is not None
        )
        return "GK1 Reviewing" if acted else "GK1 Review"
    if stage in ("Dump Data", "Manual Data Entry", "IRN Processing"):
        return "GK1 Reviewing"
    if stage == "ERP Submission":
        return "Ready for Submission"
    return stage


def _start_extraction_background(job_id: str) -> None:
    """Run extraction off the request thread, so an upload or the Extract button returns at
    once instead of sitting on OCR + model calls. Own DB session, same pattern as the parked-
    session resume in main.py: the request's session closes when the endpoint returns, long
    before this finishes.
    """
    import threading

    from app.db.session import SessionLocal

    def _go() -> None:
        db2 = SessionLocal()
        try:
            job2 = db2.get(Job, job_id)
            if job2 is None:
                return
            run_extraction(db2, job2)
        except Exception:  # noqa: BLE001
            logger.exception("background extraction failed for job %s", job_id)
            db2.rollback()
            job2 = db2.get(Job, job_id)
            # Let the operator try again rather than leave the job stuck reading "AI
            # Processing" forever with nothing actually running.
            if job2 is not None and job2.status == "extracting":
                job2.status = "draft"
                db2.commit()
        finally:
            db2.close()

    threading.Thread(target=_go, daemon=True, name=f"extract-{job_id}").start()


def _begin_extraction(db: Session, job: Job) -> None:
    """Common start-of-extraction bookkeeping for the auto-trigger, the Extract button and
    Re-run extraction alike: clear any earlier approvals — a fresh read can change the very
    values that were approved, on Data Extraction and on Data Validation both — then hand off
    to the background thread.

    Also resets where the job APPEARS to stand: a job already moved on to, say, ERP
    Submission before a Rerun kept showing "Ready for Submission" afterward even though the
    data underneath it had just been wiped and re-read from scratch — stage_override wins
    over everything in _job_stage(), and even without it the "last stage the operator
    recorded moving to" query would still find that same old entry. Fresh data means a fresh
    review, so both are reset here exactly like the approval flags above: the override is
    cleared, and a new "Data Validation" JobEvent is recorded the same way record_stage()
    does for an operator's own click, so _job_stage() has a CURRENT answer to fall back to
    instead of a stale one.
    """
    db.query(JobDocument).filter(JobDocument.job_id == job.id).update(
        {"approved": False, "gk2_approved": False}, synchronize_session=False)
    job.validation_approved = False
    job.gk2_validation_approved = False
    job.gk2_status = None
    job.accepted_verifications = None
    job.stage_override = None
    job.status = "extracting"
    db.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id, status=job.status,
                    stage="Data Validation", note="Extraction started — back to Data Validation for review"))
    db.commit()
    _start_extraction_background(job.id)


def _maybe_auto_extract(db: Session, job: Job) -> None:
    """Start extraction the moment every MANDATORY document slot is filled — the operator has
    already done what was asked; a button to also press "go read it" is a step nobody wants
    and somebody always forgets. An optional document (TemplateDocument.is_required=False)
    is never waited on. Only fires from a job's first, empty-handed "draft" state, so it
    never fires again from an upload made to replace one document on an already-extracted job
    (that stays a deliberate Re-run).
    """
    if job.status != "draft":
        return
    group = db.get(TemplateGroup, job.group_id)
    if group is None or not group.documents:
        return
    filled = {
        jd.template_document_id
        for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all()
        if jd.file_path
    }
    required = {td.id for td in group.documents if td.is_required}
    if not required.issubset(filled):
        return
    # Claim the "draft -> extracting" transition atomically before doing anything else. Two
    # of a job's last required documents uploaded via separate, near-simultaneous requests
    # (a parallel multi-file drag-and-drop, say) can each reach this exact point having read
    # job.status == "draft" a moment ago - an ordinary check-then-act race, not something the
    # plain attribute check above can close on its own. Only the request whose UPDATE actually
    # flips a still-"draft" row wins; a second one sees 0 rows affected and backs off, rather
    # than both going on to start their own full run_extraction - which was observed live to
    # duplicate every per-row custom field value it wrote, both runs' inserts surviving
    # because neither knew about the other.
    claimed = (
        db.query(Job)
        .filter(Job.id == job.id, Job.status == "draft")
        .update({"status": "extracting"}, synchronize_session=False)
    )
    db.commit()
    if not claimed:
        return
    db.refresh(job)
    _begin_extraction(db, job)


# The "Importer/Exporter" column is specifically the CONSIGNEE - not the buyer, not
# anything else a template happens to call its party field. Those used to be fallbacks,
# which meant an export template with no "consignee" field ever marked would show its
# buyer there instead - a different party, silently standing in for the one the column is
# named after. No match now means no name, same as a template with nothing marked at all;
# the caller falls back to the template's own name in that case.
_CUSTOMER_LABELS = (
    "consignee full name", "consignee name", "consignee",
)


def _customer_name(db: Session, job: Job) -> str | None:
    """The customer on this job, read off its own documents.

    The list column is headed "Customer name" and was rendering the TEMPLATE's name, which is a
    different thing entirely: every nokia(excel) job read "nokia(excel)" whoever the consignee
    turned out to be, and two customers sharing a template were indistinguishable.

    A job-level value only (row_index is None): a per-line field is a product, not a party.
    Falls back to None so the caller can show the template name as before rather than a blank.
    """
    from app.models.custom_field import CustomField

    try:
        rows = (db.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.is_(None))
                .all())
        # Same "fixed" concept JobFieldValueOut.origin uses for the API response - a
        # hardcoded custom field's id, so a value coming from one can be told apart from a
        # genuine per-job reading (a mark, an AI computation, or a reference-sheet lookup).
        # Not a column on JobFieldValue itself, so it has to be looked up here the same way.
        hardcoded_ids = {
            c.id for c in db.query(CustomField)
            .filter(CustomField.group_id == job.group_id, CustomField.kind == "hardcoded").all()
        }
    except Exception:  # noqa: BLE001 — a name is never worth failing the list over
        return None
    by_label: dict[str, tuple[str, bool]] = {}
    for fv in rows:
        val = (fv.value or "").strip()
        if not val:
            continue
        key = re.sub(r"[^a-z ]+", " ", (fv.label_name or "").lower()).strip()
        key = re.sub(r"\s+", " ", key)
        is_fixed = fv.custom_field_id in hardcoded_ids
        # First one wins: a field marked on two documents is the same field twice.
        by_label.setdefault(key, (val, is_fixed))

    # A hardcoded field is the same value on every job of this template, never a real
    # per-job reading, so it is excluded here regardless of which label it carries - a
    # template that hardcodes its consignee (one fixed importer, e.g. NOKIA's own Sea
    # Import template) should show nothing here until its consignee is a genuine per-job
    # reading (a mark or an AI field), rather than repeat a constant that tells two jobs
    # apart no better than a blank would.
    for want in _CUSTOMER_LABELS:
        for key, (val, is_fixed) in by_label.items():
            if key == want and not is_fixed:
                return _just_the_name(val)
    for want in _CUSTOMER_LABELS:
        for key, (val, is_fixed) in by_label.items():
            if want in key and not is_fixed:
                return _just_the_name(val)
    return None


def _pulled_from_sender(db: Session, job: Job) -> str | None:
    """Who actually emailed this job in, for the Assigned To column - NOT a substitute
    reading for Importer/Exporter (a person is not the consignee; putting one there just
    trades one wrong answer shown under the wrong heading for another, confusing the two
    completely different questions "who is this shipment for" and "who sent us this
    paperwork"). Only worth showing at all while the job is genuinely unassigned - the
    moment a real operator is assigned, _operator_name already answers this column, and a
    stale sender name sitting behind it would be misleading once someone real owns the job.

    If the sender's address is actually a registered user's own account - their login email,
    or a personal mailbox they connected for their own polling (User.mail_email, same idea as
    the tenant's shared inbox) - show the Full Name that was typed in when that account was
    created, not a raw fragment of the address. Only falls back to the email's own local part
    (e.g. "ftwz2" from ftwz2@4slogistics.com) when no such account exists - an external
    sender, like a customer or a CHA, genuinely has no name on file here.
    """
    if job.created_by_id or job.assigned_operator_id:
        return None
    try:
        from app.core.job_email import read_email_meta

        sender = (read_email_meta(job.id) or {}).get("sender") or ""
        if "@" not in sender:
            return None
        from sqlalchemy import func

        from app.models.user import User

        user = (
            db.query(User)
            .filter(func.lower(User.email) == sender.lower())
            .first()
            or db.query(User)
            .filter(User.mail_email.isnot(None), func.lower(User.mail_email) == sender.lower())
            .first()
        )
        if user is not None:
            return user.full_name
        return sender.split("@", 1)[0]
    except Exception:  # noqa: BLE001 — a name is never worth failing the list over
        pass
    return None


def _just_the_name(val: str) -> str:
    """The company's name, dropping the address that was marked along with it.

    A "Consignee" box on a BL or invoice is printed as a block - the name on its own first
    line, the street/city/country on the lines under it - so the mark reads the whole block
    as one value. The list column is headed "Customer name", not "Customer name and
    address", so only that first line is shown here; the full value the document actually
    carried is untouched everywhere else (Data Extraction, Dump Data, the ERP entry itself).
    """
    first = (val or "").strip().split("\n", 1)[0].strip()
    return first or val


# How a shipment travelled, and which way. Read off the JOB's own values rather than the
# template's name, because a template called "air export" turned out to carry no air waybill
# at all — the documents are the evidence, the title is a label someone typed.
#
# Two independent questions, and either can be unanswerable: a job with no bill of lading and
# no air waybill genuinely does not say how it travelled, and a blank is the honest answer.
_AIR_LABELS = ("awb", "mawb", "hawb", "flight", "airway", "air way", "airline", "airport")
_SEA_LABELS = ("vessel", "voyage", "container", "bill of lading", "waybill", "seal", "port of")
_IMPORT_LABELS = ("igm", "cth", "ritc", "aidc", "bill of entry", "customs house", "duty",
                  "be type", "beheading")
_EXPORT_LABELS = ("exporter", "lut", "shipping bill", "igst no", "buyer order", "consignor")


def _shipment_mode(db: Session, job: Job) -> str | None:
    """'Sea Import', 'Air Export' and so on.

    The template's OWN tagged mode (set by a person - Super Admin at creation, Tenant Admin
    afterwards) wins when it is set: it is a confirmed fact about the template, not a guess
    about one job's documents. A job's own paperwork is read only as a fallback, for a
    template nobody has tagged yet - and that guess can be wrong in a way a person's answer
    cannot, which is exactly what showed "Sea Export" under a genuinely air-export template
    for one job whose air waybill fields happened to come back empty.
    """
    group = db.get(TemplateGroup, job.group_id)
    if group is not None and group.mode:
        return group.mode
    try:
        rows = (db.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id)
                .all())
    except Exception:  # noqa: BLE001
        return None

    labels: list[str] = []
    mode_value = ""
    transport_value = ""
    for fv in rows:
        lab = (fv.label_name or "").lower()
        labels.append(lab)
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if not val:
            continue
        # The ICEGATE code: a single letter, S for sea and A for air.
        if lab in ("mode_of_transport", "transportmodecode") and len(val) <= 2:
            mode_value = val.upper()
        elif "transport" in lab or "mode" in lab:
            transport_value = val.upper()

    blob = " ".join(labels)

    def _mode() -> str | None:
        if mode_value.startswith("A") or "AIR" in transport_value:
            return "Air"
        if mode_value.startswith("S") or "SEA" in transport_value:
            return "Sea"
        air = sum(1 for k in _AIR_LABELS if k in blob)
        sea = sum(1 for k in _SEA_LABELS if k in blob)
        if air > sea:
            return "Air"
        if sea > air:
            return "Sea"
        return None

    def _direction() -> str | None:
        imp = sum(1 for k in _IMPORT_LABELS if k in blob)
        exp = sum(1 for k in _EXPORT_LABELS if k in blob)
        if imp > exp:
            return "Import"
        if exp > imp:
            return "Export"
        return None

    mode, direction = _mode(), _direction()
    # Half an answer is still worth showing: "Import" alone tells an operator which desk it
    # belongs to even when nothing says how it travelled.
    if mode and direction:
        return f"{mode} {direction}"
    return mode or direction


def _operator_name(db: Session, job: Job) -> str | None:
    # Prefer whoever actually created the job (a logged-in Operator); a mail-pulled job has
    # no creator, only whichever operator the customer's mailbox routes to.
    user_id = job.created_by_id or job.assigned_operator_id
    if not user_id:
        return None
    u = db.get(User, user_id)
    return u.full_name if u else None


def _compute_content_checksum(keyouted: dict) -> str | None:
    """A fingerprint of what the extracted documents actually SAY, not what file, filename or
    email they arrived in - a hash of every non-empty field value, keyed by its document
    bucket and label so the hash is inherently per-template (an Invoice's "Amount" and a
    Packing List's "Amount" can never collide with each other). Two jobs on the same template
    that hash the same are, for all practical purposes, the same shipment's paperwork.

    Deliberately built from the ALREADY-ASSEMBLED keyed-out snapshot (see the caller) rather
    than querying JobFieldValue again - the exact same values the operator sees on screen and
    the exact same values that end up in the ERP.
    """
    import hashlib

    parts: list[str] = []
    for bucket, fields in keyouted.items():
        for label, value in fields.items():
            text = ("|".join(str(v or "").strip().lower() for v in value)
                    if isinstance(value, list) else str(value or "").strip().lower())
            if text:
                parts.append(f"{bucket}::{label}::{text}")
    if not parts:
        return None
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()


def _find_duplicate_job(db: Session, job: Job) -> Job | None:
    """Same tenant, same template, an already-settled job (never another job that is itself
    still waiting on a duplicate decision — that would chain off a flag instead of a real
    job) whose extracted data hashes the same. Almost certainly the same shipment's paperwork
    arriving twice - a re-forward, a resend, the same PDF pulled from two mailboxes - however
    differently the email, filename or scan came in. `job` must already have its checksum set.
    """
    if not job.content_checksum:
        return None
    return (
        db.query(Job)
        .filter(Job.tenant_id == job.tenant_id, Job.group_id == job.group_id,
                Job.content_checksum == job.content_checksum, Job.id != job.id,
                Job.status != "possible_duplicate")
        .order_by(Job.created_at.asc())
        .first()
    )


def _duplicate_of_reference(db: Session, job: Job) -> str | None:
    if not job.duplicate_of_job_id:
        return None
    other = db.get(Job, job.duplicate_of_job_id)
    return other.reference if other else None


def _job_out(db: Session, job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    out.stage = _job_stage(db, job)
    out.outer_status = _outer_status(db, job)
    out.customer_name = _customer_name(db, job)
    out.mode = _shipment_mode(db, job)
    out.operator_name = _operator_name(db, job)
    out.pulled_from_sender = _pulled_from_sender(db, job)
    out.duplicate_of_reference = _duplicate_of_reference(db, job)
    return out


@router.get("/available-groups", response_model=list[AvailableGroup])
def available_groups(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> list[AvailableGroup]:
    """Template sets the caller can act on, scoped by tenant + role:
    - Operator: only tenant-admin-APPROVED sets are runnable.
    - Tenant Admin: sets the super admin finished (ready / approved / changes_requested) to review.
    - Super Admin: any non-draft set (for testing).
    - GK2/Manager: never start a job, but the jobs list page calls this in the same breath
    as listing jobs (Promise.all) - refusing it here failed the whole page, not just hid
    a "+ New Job" button they were never going to use anyway."""
    if user.role in (OPERATOR, GK2, MANAGER):
        allowed = ("approved",)
    else:
        allowed = ("ready", "approved", "changes_requested")
    query = scoped_query(db, TemplateGroup, scope).filter(TemplateGroup.status.in_(allowed))

    # If this user has explicit template assignments, restrict to them (super admins
    # are never restricted). No assignments = access all of their tenant's templates.
    if user.role != SUPER_ADMIN:
        from app.models.user_template import UserTemplateAssignment

        assigned = [
            a.group_id
            for a in db.query(UserTemplateAssignment).filter(UserTemplateAssignment.user_id == user.id).all()
        ]
        if assigned:
            query = query.filter(TemplateGroup.id.in_(assigned))

    groups = query.order_by(TemplateGroup.created_at.desc()).all()
    return [AvailableGroup.model_validate(g) for g in groups]


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobOut:
    group = db.get(TemplateGroup, payload.group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template set not found")
    # Operators may only run their own tenant's template sets.
    if user.role == OPERATOR and group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template set not found")

    job = Job(
        tenant_id=group.tenant_id,
        group_id=group.id,
        reference=(payload.reference or "").strip() or generate_job_no(),
        status="draft",
        created_by_id=user.id,
        # An operator uploading their own job's documents already IS its owner - the Jobs
        # list' own Assigned To dropdown says so from the start now, not just the read-only
        # name a non-admin used to see (_operator_name already fell back to created_by_id
        # there, but the admin dropdown only ever reflected THIS field, so an admin viewing
        # the same job saw "Unassigned" for a job someone was plainly already working).
        # Confirmed with the user: this does take the job out of every OTHER operator's
        # shared-template queue (see list_jobs' own OPERATOR visibility rule) - accepted
        # tradeoff, not an oversight. A Super Admin/Admin creating a job on someone else's
        # behalf is a different case - that job stays genuinely unassigned until a real
        # operator is picked, same as before.
        assigned_operator_id=user.id if user.role == OPERATOR else None,
    )
    db.add(job)
    db.flush()
    # One empty job-document slot per declared document in the set.
    for tdoc in group.documents:
        db.add(
            JobDocument(
                tenant_id=group.tenant_id,
                job_id=job.id,
                template_document_id=tdoc.id,
            )
        )
    db.commit()
    db.refresh(job)
    return _job_out(db, job)


def _delete_job_cascade(db: Session, job: Job) -> list[Path]:
    """Delete a job and everything that hangs off it - field values, documents. Also,
    unlike a single document's own removal (see _remove_document_file, which does this per
    file), the actual files on disk for every one of them: without this, every job delete -
    through this endpoint or custom_filter_pages.py's old-job sweep, the only two ways a job
    is ever removed - left its uploaded documents, IRN supporting documents and (for a
    mail-routed job) its saved original email and attachments sitting on disk forever (the
    DB rows go: SupportingDocument cascades at the database's own FK, ON DELETE CASCADE; the
    others are deleted explicitly below).

    Deliberately does NOT delete anything from disk itself - same reasoning as
    _remove_document_file: that is irreversible the instant it happens, while the caller's
    own db.commit() can still fail or roll back afterward. Returns the directories to
    delete, which the caller must only actually remove once its commit has succeeded. Does
    not commit; the caller decides the transaction boundary."""
    from app.core.job_email import job_email_dir
    from app.models.supporting_document import SupportingDocument

    dirs = [_job_doc_dir(jd.id)
            for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all()]
    dirs += [_supporting_doc_dir(sd.id)
             for sd in db.query(SupportingDocument).filter(SupportingDocument.job_id == job.id).all()]
    dirs.append(job_email_dir(job.id))

    db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).delete()
    db.query(JobDocument).filter(JobDocument.job_id == job.id).delete()
    db.delete(job)
    return dirs


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN)),
) -> None:
    """Delete a job and its documents/values. Operators may delete their own or the
    mail-routed jobs assigned to them; admins any job in scope."""
    job = _load_job(db, job_id, scope, user)
    dirs = _delete_job_cascade(db, job)
    db.commit()
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


# Server-side mirror of JobsOverview.tsx's own bucket math on the dashboard - both have to
# agree on what "this week"/"this month" mean, or a box's count and what pressing it actually
# shows would disagree. Monday-start week, calendar month, reckoned from the server's own
# clock (jobs have no timezone of their own either).
def _bucket_bounds(bucket: str) -> tuple[datetime, datetime] | None:
    if bucket == "all" or bucket not in ("today", "week", "month"):
        return None
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    if bucket == "today":
        return today_start, today_start + timedelta(days=1)
    if bucket == "week":
        monday = today_start - timedelta(days=today_start.weekday())
        return monday, monday + timedelta(days=7)
    # month
    month_start = today_start.replace(day=1)
    next_month = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    return month_start, next_month


def _apply_dashboard_filter(query, group: str, bucket: str):
    """The same populations as the operator/manager dashboard boxes - see JobsOverview.tsx.
    `group` picks which one; `bucket` narrows it to a calendar window (or "all" for none)."""
    bounds = _bucket_bounds(bucket)
    if group == "pending":
        query = query.filter(Job.status.notin_(["completed", "failed", "duplicate"]))
        # Once a job is handed to GK2 (any non-null gk2_status - pending, preparing_erp,
        # entering_erp, submitted, or failed) it is no longer "pending" in GK1's own queue,
        # even though its raw status is still "extracted". Without this, a job counted here
        # AND in "Pending Approval" the moment GK1 submitted it - the same job inflating two
        # different boxes' totals at once.
        query = query.filter(Job.gk2_status.is_(None))
        date_col = Job.created_at
    elif group == "eta":
        query = query.filter(Job.eta_date.isnot(None))
        if bounds:
            start, end = bounds
            # eta_date is a plain "YYYY-MM-DD" string column - ISO dates sort lexicographically
            # in calendar order, so string comparison against the bucket's own bounds is exact.
            query = query.filter(Job.eta_date >= start.date().isoformat(),
                                 Job.eta_date < end.date().isoformat())
        return query
    elif group == "approval":
        query = query.filter(Job.gk2_status == "pending")
        date_col = Job.created_at
    elif group == "completed":
        # Exactly the condition _outer_status uses to produce "AI - ERP Submitted" - the
        # manager dashboard's "Completed" box.
        query = query.filter(Job.gk2_status == "submitted")
        date_col = Job.created_at
    else:
        return query
    if bounds:
        start, end = bounds
        query = query.filter(date_col >= start, date_col < end)
    return query


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
    # None (the default) returns everything, unchanged - existing callers (the dashboard's own
    # box counts, which need the full set to total) are unaffected. The Jobs list page passes
    # an explicit limit to page through a large list instead of loading it all in one go.
    limit: int | None = None,
    offset: int = 0,
    # Pressing one of the dashboard's boxes navigates here with these two set, so the list
    # shows exactly what the box counted - see _apply_dashboard_filter.
    group: str | None = None,
    bucket: str = "all",
    # The IRN Pending list (frontend/src/pages/jobs/IrnPendingPage.tsx) passes this to show
    # only jobs GK2 has parked in "IRN Document Process" - independent of group/bucket, which
    # is why it is its own param rather than folded into _apply_dashboard_filter's presets.
    gk2_status: str | None = None,
) -> list[JobOut]:
    query = scoped_query(db, Job, scope)
    # Operators see shared jobs (no owner) plus the mail-routed jobs assigned to them —
    # never another operator's routed jobs. Admins see everything in scope.
    if user.role == OPERATOR:
        if user.assigned_modes:
            # An operator assigned to one or more shipment modes (Sea Import, Sea Export,
            # ...) sees EVERY job of those modes, full stop - same rule GK2 already uses
            # below, and it REPLACES the ownership-based rule entirely for these operators:
            # who pulled/uploaded/was routed a job no longer matters once a mode gate is
            # set. This mirrors GK2's own mode filtering intentionally, so the two roles
            # behave consistently.
            query = query.join(TemplateGroup, Job.group_id == TemplateGroup.id).filter(
                TemplateGroup.mode.in_(user.assigned_modes)
            )
        else:
            # No mode assigned: unchanged legacy behaviour - shared/unowned jobs plus
            # whatever was routed to this operator by name, narrowed by explicit template
            # access if any was granted.
            query = query.filter(
                (Job.assigned_operator_id == user.id) | (Job.assigned_operator_id.is_(None))
            )
            # An operator given explicit template access (Sea Import, Sea Export, ...) has
            # that access instead of general job visibility, not on top of it — the same rule
            # available_groups already applies to which templates they can start a job on.
            # Without this, an operator assigned only to two templates could still see every
            # OTHER template's unowned jobs, which is exactly the access they were not given.
            from app.models.user_template import UserTemplateAssignment

            assigned = [
                a.group_id
                for a in db.query(UserTemplateAssignment)
                .filter(UserTemplateAssignment.user_id == user.id).all()
            ]
            if assigned:
                query = query.filter(Job.group_id.in_(assigned))
    elif user.role == GK2:
        # Every job that has ever reached GK2's queue — pending, being prepared for ERP, or
        # already submitted — not just "still waiting on me". A job GK2 already approved must
        # stay visible in their own list instead of vanishing the moment they act on it; only
        # a job still with an operator (gk2_status still null) is excluded. Further narrowed
        # to the shipment modes they were assigned at creation (see User.assigned_modes); no
        # modes assigned means no jobs, not "unrestricted" — a mode must be picked to see
        # anything.
        query = query.filter(Job.gk2_status.isnot(None))
        if user.assigned_modes:
            query = query.join(TemplateGroup, Job.group_id == TemplateGroup.id).filter(
                TemplateGroup.mode.in_(user.assigned_modes)
            )
        else:
            query = query.filter(False)
    elif user.role == MANAGER:
        # Read-only observer over the WHOLE tenant, on purpose - every job, every GK1/GK2's
        # work, unrestricted by template, mode, or gk2_status. Bounded only by scoped_query's
        # tenant filter above.
        pass

    if group is not None:
        query = _apply_dashboard_filter(query, group, bucket)
    if gk2_status is not None:
        query = query.filter(Job.gk2_status == gk2_status)

    query = query.order_by(Job.created_at.desc())
    if limit is not None:
        query = query.offset(offset).limit(limit)

    jobs = query.all()
    return [_job_out(db, j) for j in jobs]


# The path spells out /jobs itself: this router is mounted with NO prefix, so "/{job_id}/
# history" registered at the API root instead - it 404'd, and it also sat there as a
# greedy two-segment route able to shadow anything else at the top level.
class StopJob(BaseModel):
    reason: str | None = None
    # Only for a run that is genuinely still going: the browser cannot be killed from here, so
    # marking it failed while it is still driving the ERP means the entry may land anyway.
    force: bool = False


@router.post("/jobs/{job_id}/stop", response_model=JobDetailOut)
def stop_job(
    job_id: str,
    payload: StopJob,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Stop a job that is stuck on `processing`, and mark it failed.

    Nothing could do this before. `status` is set to `processing` before the entry starts and
    only rewritten when it finishes, so a run whose thread died - which is what happens every
    time the server is restarted under one - left the job saying `processing` for ever. It
    could not be re-run, could not be completed, and showed as Running on every screen.
    """
    job = scoped_query(db, Job, scope).filter(Job.id == job_id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # Same access rule as _load_job: mode-assigned operators are gated by mode instead of
    # ownership.
    if user.role == OPERATOR:
        if user.assigned_modes:
            group = db.get(TemplateGroup, job.group_id)
            if group is None or group.mode not in user.assigned_modes:
                raise HTTPException(status_code=404, detail="Job not found")
        elif job.assigned_operator_id not in (None, user.id):
            raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "processing":
        raise HTTPException(status_code=409,
                            detail=f"This job is not running - it is {job.status!r}.")
    if live_runs.is_alive(job_id) and not payload.force:
        raise HTTPException(
            status_code=409,
            detail=("This entry is still going - it sent a screen moments ago. Stopping it here "
                    "marks the job failed but cannot close the browser, so the entry may still "
                    "land in the ERP. Send force=true if you mean to do that anyway."),
        )
    job.status = "failed"
    job.erp_status = "stopped"
    job.erp_reason = payload.reason or (
        "Stopped by hand. The entry was not running any more - most likely the server was "
        "restarted while it was in progress."
    )
    db.commit()
    live_runs.clear(job_id)
    db.refresh(job)
    return _build_detail(db, job)


def _put_back_on_entry(db: Session, job: Job) -> None:
    """Clear THIS job's failed run and stand it ready to enter again. Values are untouched.

    A re-run is not a restart. The operator has usually corrected one box on the screen and
    wants the entry attempted again with everything else exactly as it was - so nothing here
    deletes a value, an approval or a document. Only the record of the LAST RUN is cleared, and
    only for this job.

    ONE implementation, because two routes lead here and they must leave the job identical:
    "Ready for entry" on the admin side and "Rerun job" on the operator's.
    """
    if job.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is running. Stop it before putting it back on ERP Entry.")
    if job.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("This job has already been entered into the ERP. Running it again would "
                    "enter it twice."))
    if job.status != "failed":
        # Both docstrings above say "failed" explicitly - this exists to retry an ERP run
        # that broke on one step, not to fast-forward a job that has never reached ERP Entry
        # at all. Without this, "Rerun job" being visible on every job page regardless of
        # state let it skip a job straight from Data Validation to "Ready for Submission" -
        # past its own GK1 review - just by being pressed early.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has no failed ERP run to retry.")
    job.status = "extracted"
    job.stage_override = "ERP Submission"
    # The old failure must not keep showing on a job that is being set up to run again. The LOG
    # and the AI's reading of the failure screen go with it: they describe a run that is no
    # longer this job's state, and leaving them under a job that says "ready" is how the ERP
    # Entry screen ended up showing "The last ERP entry failed" above a form about to run
    # again perfectly well.
    job.erp_status = None
    job.erp_reason = None
    job.erp_failed_field = None
    job.erp_failed_value = None
    job.erp_log = None
    job.erp_diagnosis = None
    db.commit()
    live_runs.clear(job.id)
    db.refresh(job)


@router.post("/jobs/{job_id}/ready-for-entry", response_model=JobDetailOut)
def ready_for_entry(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> JobDetailOut:
    """Put a failed job back on ERP Entry, ready to run, WITHOUT re-extracting it.

    The only way to make a failed job runnable was "Run failed job again", which resets it to
    draft and re-extracts - and re-extraction throws away every value the operator typed. The
    four duty notifications on this customer's jobs were retyped three times in one afternoon
    for exactly that reason, on a job whose only problem was one bad step in the ERP script.

    Nothing about the job's data is touched. The stage is pinned to ERP Entry so a cross-check
    that is already understood does not push it back to Verification.
    """
    job = _load_job(db, job_id, scope, user)
    _put_back_on_entry(db, job)
    return _build_detail(db, job)


_STEP_LINE = re.compile(r"^step (\d+)(\D|$)")


def _script_steps_for(db: Session, job: Job) -> int:
    """How many steps this job's ERP script has, so a progress bar has a length even before
    the first live frame arrives - and after the frames have been dropped."""
    from app.models.erp_script import ErpScript

    try:
        script = next(
            (s for s in db.query(ErpScript).filter(ErpScript.tenant_id == job.tenant_id,
                                                   ErpScript.status == "ready").all()
             if job.group_id in (s.template_ids or [])), None)
        return len(script.steps or []) if script else 0
    except Exception:  # noqa: BLE001 — a bar must never break the screen it is on
        return 0


def run_progress(log, debug: dict | None, total_hint: int | None = None) -> dict:
    """How far through its steps a run has got: {done, total, percent}.

    Derived rather than stored, from two sources that cover the whole life of a run:

      - while it is going, the live frame knows exactly which step it is ON and how many there
        are ("step 23 of 30"), which is the only thing that can report a step still in flight;
      - once it is over the frames are dropped, but the LOG is kept - and every line begins
        "step N", so the highest N is how far it reached. That is what lets a job opened
        tomorrow still show how far it got.

    A bar drawn off nothing is worse than no bar, so `total` is 0 when nothing knows the
    length, and the screen can leave it out.
    """
    lines = log or []
    if isinstance(lines, str):
        lines = lines.splitlines()
    done = 0
    for line in lines:
        m = _STEP_LINE.match(str(line).strip())
        if m:
            done = max(done, int(m.group(1)))
    total = int((debug or {}).get("of") or 0) or int(total_hint or 0)
    # The step the run is sitting on is not finished yet - the log line for it comes after.
    at = int((debug or {}).get("step") or 0)
    if at:
        done = max(done, at - 1)
    if total:
        done = min(done, total)
    return {"done": done, "total": total,
            "percent": int(round(done * 100 / total)) if total else 0}


@router.get("/jobs/{job_id}/live")
def job_live(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> dict:
    """Where a running ERP entry has got to: the screen it is on, and the log so far.

    Poll this while a job is `processing` to watch the entry as it happens, rather than
    waiting for it to finish and reading the one screenshot it left behind.
    """
    job = _load_job(db, job_id, scope, user)
    frame = live_runs.latest(job_id)
    if frame is None:
        # Nothing live, so there is no picture: a run's screenshot is NEVER stored on the job.
        # It is put into the response at the moment the run ends and is not persisted anywhere,
        # so `job.erp_screenshot` does not exist and asking for it 500'd this endpoint for
        # every job. The last frame of a finished run is held by live_runs for a few minutes;
        # after that the log is all there is.
        # erp_log is a JSON LIST of lines, not a string, so take it as it comes.
        past = job.erp_log or []
        if isinstance(past, str):
            past = past.splitlines()
        return {"live": False, "status": job.status, "screenshot": None,
                "log": list(past), "debug": None,
                "progress": run_progress(past, None, _script_steps_for(db, job))}
    return {"live": not frame["done"], "status": job.status,
            "screenshot": frame["shot"], "log": frame["log"],
            "debug": frame.get("debug"),
            "progress": run_progress(frame["log"], frame.get("debug"),
                                     _script_steps_for(db, job))}


class JobEtaUpdate(BaseModel):
    # "YYYY-MM-DD", or None to clear it. A plain calendar date - a job has no timezone of its
    # own, so there is no "when" to attach to a time-of-day component.
    eta_date: str | None = None


@router.patch("/jobs/{job_id}/eta", response_model=JobDetailOut)
def set_job_eta(
    job_id: str,
    payload: JobEtaUpdate,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """GK1 sets (or clears) this job's target date, from the date-picker on the Jobs list.
    Drives the ETA boxes on the operator dashboard - see JobsOverview.tsx."""
    job = _load_job(db, job_id, scope, user)
    if payload.eta_date is not None:
        try:
            date.fromisoformat(payload.eta_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="eta_date must be a valid YYYY-MM-DD date",
            )
    job.eta_date = payload.eta_date or None
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


class JobAssignUpdate(BaseModel):
    # None clears the assignment (job goes back to "unassigned"/shared).
    operator_id: str | None = None


@router.patch("/jobs/{job_id}/assign", response_model=JobDetailOut)
def assign_job_operator(
    job_id: str,
    payload: JobAssignUpdate,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Set (or clear) which operator this job is assigned to, from the dropdown on the Jobs
    list. Always editable - reassigning simply overwrites the previous value, no locking."""
    job = _load_job(db, job_id, scope, user)
    if payload.operator_id:
        operator = db.get(User, payload.operator_id)
        if (
            not operator
            or operator.tenant_id != job.tenant_id
            or operator.role != OPERATOR
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="operator_id must be an operator in the same tenant",
            )
    job.assigned_operator_id = payload.operator_id or None
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


class StageMark(BaseModel):
    stage: str


@router.post("/jobs/{job_id}/stage", status_code=status.HTTP_204_NO_CONTENT)
def record_stage(
    job_id: str,
    payload: StageMark,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> None:
    """Record that the operator finished a stage and moved on.

    The history was built from status changes alone, and the backend has five of those — so
    it could never show the seven stages an operator actually works through. Moving from Data
    Validation to Dump Data changes no status and left no trace, and "Documents extracted ->
    Completed" is the whole story of a job that took someone an hour.

    Recorded only when the stage really moves: pressing Next twice, or clicking back through
    the rail, must not fill the history with noise.
    """
    job = _load_job(db, job_id, scope, user)
    stage = (payload.stage or "").strip()[:20]
    if not stage:
        return None
    last = (
        db.query(JobEvent)
        .filter(JobEvent.job_id == job.id, JobEvent.stage.isnot(None))
        .order_by(JobEvent.created_at.desc())
        .first()
    )
    if last is not None and (last.stage or "") == stage:
        return None
    db.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id, status=job.status,
                    stage=stage, note=f"Moved on to {stage}"))
    db.commit()
    return None


class DocumentApprove(BaseModel):
    approved: bool = True


@router.post("/jobs/{job_id}/documents/{job_document_id}/approve", response_model=JobDetailOut)
def approve_document(
    job_id: str,
    job_document_id: str,
    payload: DocumentApprove,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> JobDetailOut:
    """Operator (GK1) presses "Submit for Approval" on ONE document, on Data Extraction —
    GK2 sees the same button labelled "Approve & Proceed" and re-reviews the SAME document
    independently: GK2's press is recorded on its own column (gk2_approved), never GK1's
    (approved), so GK2 opening a job GK1 already approved sees it as unapproved from their
    own side and has to press through it themselves. Every uploaded document on the job
    needs this (from whichever reviewer is looking at it) before the screen lets Next
    through — see blockedBecause("extraction") on the frontend, which is the actual gate."""
    job = _load_job(db, job_id, scope, user)
    jdoc = (
        db.query(JobDocument)
        .filter(JobDocument.id == job_document_id, JobDocument.job_id == job.id)
        .first()
    )
    if jdoc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if user.role == GK2:
        jdoc.gk2_approved = payload.approved
    else:
        jdoc.approved = payload.approved
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/validation/approve", response_model=JobDetailOut)
def approve_validation(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> JobDetailOut:
    """Operator (GK1) presses "Approved and Proceed" on Data Validation - GK2 re-reviews the
    same job independently and presses their OWN copy of this button, recorded on its own
    column (gk2_validation_approved), never GK1's (validation_approved). Deliberately
    unconditional either way - it accepts the data even over an open Mismatch/Review flag,
    because pressing it IS the decision that whatever is flagged is fine to move on with. The
    cross-checks stay visible on the screen regardless; this button says a human looked, not
    that nothing was found."""
    job = _load_job(db, job_id, scope, user)
    if user.role == GK2:
        job.gk2_validation_approved = True
    else:
        job.validation_approved = True
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/gk2/submit-for-approval", response_model=JobDetailOut)
def submit_for_gk2_approval(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Operator (GK1) presses "Final Submit for Approval" on ERP Submission — hands the job
    to a GK2 user instead of running the real ERP entry. See Job.gk2_status."""
    job = _load_job(db, job_id, scope, user)
    if job.status != "extracted" or not job.validation_approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cross Docs Verification must be approved before this can go to GK2.",
        )
    job.gk2_status = "pending"
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/gk2/reopen", response_model=JobDetailOut)
def reopen_gk2_submission(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> JobDetailOut:
    """Put a job GK2 already approved back to "Pending GK2 Approval", for a genuine
    re-submission - most often a job that finished under the OLD placeholder approval (before
    a real ERP run existed behind it) and now needs to actually go through the ERP for real.

    Nothing about the job's data, documents or approvals is touched - only the two fields
    that decide where it sits: job.status back to "extracted" (submit-for-approval requires
    this, and it is what "Cross Docs Verification approved, not yet entered" means) and
    gk2_status back to "pending" (see Job.gk2_status). An admin action, not something GK2
    presses themselves - unlike a build/run failure sending a job back to "pending" on its
    own (see gk2_approve), this reopens a job GK2 already finished.
    """
    job = _load_job(db, job_id, scope, user)
    if job.gk2_status != "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has not been through GK2 approval, so there is nothing to reopen.",
        )
    job.status = "extracted"
    job.gk2_status = "pending"
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/gk2/sync-status", response_model=JobDetailOut)
def sync_gk2_status(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> JobDetailOut:
    """Correct a stale gk2_status left over from an earlier attempt, on a job whose real outcome
    (job.status) already reads "completed" - persist_run_outcome never touches gk2_status (see
    its own docstring), so a job that later succeeded through a path other than gk2_approve's
    own background thread (an Entry Browser rerun, for one) can be genuinely done while
    _outer_status still reads off whatever gk2_status the run happened to be sitting on the
    moment that path took over - "failed" from an earlier attempt, or an in-flight value like
    "entering_erp" a rerun started from but never itself advanced, forever.

    Narrow on purpose: only ever moves gk2_status to "submitted", and only once job.status
    already says "completed" — it does not touch, retry, resubmit, or otherwise run anything.
    """
    job = _load_job(db, job_id, scope, user)
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job has not actually completed yet — there is nothing to sync.",
        )
    if job.gk2_status == "submitted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="gk2_status already says \"submitted\", so there is nothing to fix here.",
        )
    job.gk2_status = "submitted"
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/gk2/approve", response_model=JobDetailOut)
def gk2_approve(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(GK2, SUPER_ADMIN)),
) -> JobDetailOut:
    """GK2 presses "Final Approve & Proceed" — moves to "preparing_erp" (shown as "AI -
    Preparing for ERP") right away, and a background thread does the actual work, landing on
    a REAL outcome instead of the fixed 5-second delay this used to be. The frontend already
    polls after calling this (see gk2ApproveAndProceed in JobRunPage.tsx), written for exactly
    this shape - built once for the old placeholder, unchanged for the real thing.

    For an Excel-entry customer (see TemplateGroup.entry_mode / excel_config): this is the
    SAME real ERP run complete_job's Submit Entry does - the job's import workbook is built
    ("AI - Preparing for ERP"), the tenant's ready ErpScript is then replayed against the real
    ERP with that workbook attached to its recorded upload step ("ERP Entry Process Started"),
    and the outcome is whatever the ERP actually did. Success (the run completed, or completed
    with a captured reference) shows "AI - ERP Submitted"; anything the run could not get past
    - the script itself failing, the workbook coming out empty, no ready script configured at
    all - shows "Failed", with the reason attached to erp_status/erp_reason (the existing
    "last ERP entry failed" banner already shows these regardless of role). A failed run is
    not a dead end: GK2 can press Final Approve & Proceed again themselves once whatever's
    wrong is fixed, with no GK1 or admin step in between - see the "pending" OR "failed" check
    just below.

    A template NOT set up for Excel entry keeps the OLD placeholder outcome (always
    "submitted", nothing real happens) until a real submission path exists for it too - only
    the "was this synchronous" gap is fixed everywhere; "was this real" is fixed only where an
    Excel mapping already makes it possible.
    """
    job = _load_job(db, job_id, scope, user)
    if job.gk2_status not in ("pending", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is not waiting on a GK2 approval.",
        )
    if not job.gk2_validation_approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve Cross Docs Verification (GK2's own review) before the final approval.",
        )
    # The same pre-flight complete_job requires before it touches the real ERP - a GK2 run is
    # no less real now, so a required field the operator never answered, or a job parked on
    # the ruling's own hold question, must block it here exactly as it would block Submit Entry.
    pending = _unanswered_operator_fields(db, job)
    if pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Enter a value for these fields before submitting the entry: "
                    + ", ".join(pending)),
        )
    if job.stage_override == HOLD_STAGE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is on hold — answer the required-documents question before submitting the entry.",
        )

    # GK1 chose Approval for IRN (not Skip) on IRN Documents Upload - the real ERP submission
    # does not run yet. Park the job in its own wait-state instead, next to the per-document
    # DSC + IRN Number action (still a placeholder - see Job.irn_approval_requested). Skip
    # (irn_approval_requested stays False, including for every job from before this existed)
    # falls straight through to the real submission below, unchanged.
    if job.irn_approval_requested:
        job.gk2_status = "irn_document_process"
        db.commit()
        db.refresh(job)
        return _build_detail(db, job)

    _kick_off_real_erp_submission(db, job)
    return _build_detail(db, job)


def _kick_off_real_erp_submission(db: Session, job: Job) -> None:
    """The background-thread kickoff gk2_approve's own real-submission path uses - factored
    out so the IRN-signing completion path (app/api/v1/public_irn.py's sign endpoint, once
    every document header on a GK1-Approval-for-IRN job has its DSC-signed file + IRN number)
    can trigger the exact same real ERP run rather than a second copy that could drift from
    what GK2 pressing "Final Approve & Proceed" itself does."""
    grp = db.get(TemplateGroup, job.group_id)
    is_excel_entry = grp is not None and (grp.entry_mode or "fields") == "excel"
    job.gk2_status = "preparing_erp"
    # Clear the LAST attempt's result the moment a new one starts - otherwise the "ERP
    # replay result" banner (job.erp_status, shown regardless of gk2_status) keeps showing
    # "Entered into the ERP and submitted" from a previous run while THIS run is still only
    # "ERP Entry Process Started", which is exactly the stale, misleading combination a
    # screenshot caught: the badge said one thing and the banner below it said another.
    job.erp_status = None
    job.erp_reason = None
    job.erp_diagnosis = None
    job.erp_failed_field = None
    job.erp_failed_value = None
    db.commit()
    db.refresh(job)

    import threading
    import time

    from app.db.session import SessionLocal

    job_id = job.id

    def _go() -> None:
        # Building the workbook genuinely takes well under a second - too fast for anyone to
        # actually see "AI - Preparing for ERP" before it's already replaced by the outcome.
        # This pause changes WHEN the already-decided outcome is allowed to land, never WHAT
        # it is - the work and its real result happen after it, not during it.
        time.sleep(2)
        db2 = SessionLocal()
        try:
            _run_gk2_submission(db2, job_id, is_excel_entry)
        finally:
            db2.close()

    threading.Thread(target=_go, daemon=True, name=f"gk2-erp-{job_id}").start()


def _run_gk2_submission(db: Session, job_id: str, is_excel_entry: bool) -> None:
    """The actual work behind GK2's approval, factored out of gk2_approve's background
    thread so it can be called directly - with the test's own db_session, no threading, no
    separate SessionLocal - rather than only reachable through a real background thread
    against the production database, where a test's isolated session could never see it.

    Does nothing if the job has moved on already (a second, racing call - or, in a test, a
    thread that fired for real against the production database, which never has the job the
    test cares about; harmless, since the caller checks the job's state directly afterward).

    For an Excel-entry customer this is the SAME real ERP run complete_job's Submit Entry
    does: build the workbook ("AI - Preparing for ERP"), find the tenant's ready ErpScript for
    this template, attach the workbook to its recorded upload step, and replay it against the
    real ERP ("ERP Entry Process Started") - not just build the file and call that success.
    persist_run_outcome is the one function anything that runs an entry goes through (see
    complete_job), so this reuses it rather than a second copy that could drift from what
    Submit Entry actually does.
    """
    job = db.get(Job, job_id)
    if job is None or job.gk2_status != "preparing_erp":
        return
    # This whole flow keeps job.status at "extracted" throughout, on purpose - a failure marks
    # gk2_status "failed" (shown as "Failed") rather than failing the JOB itself, precisely so
    # a retry needs nothing from GK1/an admin (see gk2_approve's "pending" OR "failed" check).
    # That only holds if a later SUCCESS also clears any "failed" a job was left carrying from
    # before this retry behaviour existed (deploy69) - otherwise the badge (from
    # gk2_status/erp_status) and job.status quietly disagree forever: "AI - ERP Submitted"
    # showing on a job whose own status still says "failed".
    job.status = "extracted"
    if not is_excel_entry:
        job.gk2_status = "submitted"
        db.commit()
        return

    def _fail(reason: str) -> None:
        # Not a dead end - GK2 sees "Failed" plus the reason (the existing "last ERP entry
        # failed" banner already shows erp_status/erp_reason regardless of role), fixes
        # whatever needs fixing, and presses Final Approve & Proceed again themselves. Nothing
        # here makes the job itself un-submittable the way a truly broken job would be, so it
        # does not need GK1 or an admin to intervene before a retry.
        job.gk2_status = "failed"
        job.erp_status = "error"
        job.erp_reason = reason
        db.commit()
        logger.warning("job %s: GK2 approval failed - %s", job_id, reason)

    from app.core.browser import _resolve_upload, play_steps
    from app.models.erp_script import ErpScript

    script = next(
        (
            s
            for s in db.query(ErpScript).filter(
                ErpScript.tenant_id == job.tenant_id, ErpScript.status == "ready").all()
            if job.group_id in (s.template_ids or [])
        ),
        None,
    )
    if script is None:
        _fail("No ready ERP script is configured for this template.")
        return

    dl_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
    dl_dir.mkdir(parents=True, exist_ok=True)
    try:
        book = _build_job_excel(db, job, out_dir=dl_dir)
    except ExcelBuildFailed as exc:
        _fail(str(exc))
        return

    grp = db.get(TemplateGroup, job.group_id)
    cfg = grp.excel_config or {}
    values, rows = entry_values_and_rows(db, job)

    # Documents the operator uploaded, so an `upload` step can attach one by name, plus the
    # workbook just built - under a fixed name, its own file name, and the template's
    # configured file name, so a step recorded against any of those still resolves.
    up_map: dict[str, str] = {}
    for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
        if not jd.file_path:
            continue
        tdoc = (db.get(TemplateDocument, jd.template_document_id)
                if jd.template_document_id else None)
        for key in filter(None, (tdoc.name if tdoc else None, Path(jd.file_path).name)):
            up_map.setdefault(str(key), str(jd.file_path))
    up_map.setdefault("excel_import", str(book))
    up_map.setdefault(book.name, str(book))
    if cfg.get("file_name"):
        up_map.setdefault(str(cfg["file_name"]), str(book))

    # The workbook only reaches the ERP through a recorded `upload` step - refuse before
    # touching the ERP, same as complete_job, rather than submit an import with no file and
    # still report it as if it went in.
    if not any(
        st.get("action") == "upload"
        and _resolve_upload(str(st.get("value") or ""), up_map) == str(book)
        for st in (script.steps or [])
    ):
        others = sorted({
            str(st.get("value") or "").strip()
            for st in (script.steps or [])
            if st.get("action") == "upload" and str(st.get("value") or "").strip()
        })
        _fail(
            f"This customer is set to import a spreadsheet, and {book.name} was built for "
            f"this job, but the ERP script {script.name!r} has no upload step that attaches it"
            + (f" (its upload steps attach {', '.join(others)})" if others
               else " (it has no upload step at all)")
            + ". Record the ERP's import screen: press its Browse button and pick any "
              "spreadsheet, and every job will attach its own instead."
        )
        return

    login = (
        {"username": script.login_username or "", "password": script.login_password or ""}
        if script.has_login else None
    )

    def _live(shot, log, debug=None, _jid=job.id):
        live_runs.publish(_jid, shot, log, debug=debug)

    # Everything up to here was preparation - "AI - Preparing for ERP" is still accurate. Only
    # from here does the run actually touch the real ERP, so that is when the badge moves to
    # "ERP Entry Process Started" - a visibly different, longer-running stage from a workbook
    # build that finishes in under a second.
    job.gk2_status = "entering_erp"
    db.commit()

    try:
        result = play_steps(
            script.url, login, script.steps or [], values, headless=True, rows=rows,
            downloads_dir=dl_dir, uploads=up_map, progress=_live,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"The ERP entry could not be run: {exc}")
        return
    live_runs.publish(job.id, None, result.get("log") or [], done=True)

    persist_run_outcome(db, job, script, result, operator_id=job.assigned_operator_id)
    db.refresh(job)
    if job.status == "failed":
        # persist_run_outcome's own terminology for a run the ERP rejected - GK2 gets the same
        # retry-yourself path every other failure here gets, not a dead end needing GK1/admin.
        job.status = "extracted"
        job.gk2_status = "failed"
        db.commit()
        logger.warning("job %s: GK2 approval failed - %s", job_id, job.erp_reason)
    else:
        job.gk2_status = "submitted"
        db.commit()
        logger.info("job %s: GK2 approved - ERP entry %s", job_id, job.status)


@router.get("/jobs/{job_id}/erp-excel")
def download_job_excel(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    """"Download as Excel" - GK1's Final Submit for Approval and GK2's Final Approve &
    Proceed screens both offer this for an Excel-entry job: the same workbook GK2's real
    approval builds (see gk2_approve), available on demand and always rebuilt fresh from the
    job's CURRENT data, so a download after a correction never hands back a stale copy."""
    job = _load_job(db, job_id, scope, user)
    try:
        book = _build_job_excel(db, job)
    except ExcelBuildFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        book, filename=book.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/jobs/{job_id}/dump-preview")
def dump_preview(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, TENANT_ADMIN, SUPER_ADMIN, ADMIN, GK2, MANAGER)),
) -> dict:
    """What the dump will actually contain, before anything is submitted.

    Until now the workbook was built INSIDE the ERP run and never shown, so a wrong column or
    an empty sheet was only discovered when the ERP rejected the import — after the browser
    had driven thirty steps. This lays the same thing out beforehand.

    It calls the SAME entry_values_and_rows and the SAME sheet plans the run uses, so what is
    shown here is what gets written. A preview computed a second way would eventually differ
    from the file, and would be believed.
    """
    job = _load_job(db, job_id, scope, user)
    group = db.get(TemplateGroup, job.group_id) if job.group_id else None
    mode = (group.entry_mode if group else None) or "fields"
    values, rows = entry_values_and_rows(db, job)

    if mode != "excel":
        # No workbook for this template — the fields themselves are what is handed over.
        flat = [{"field": k, "value": v} for k, v in sorted(values.items()) if k]
        return {"mode": mode, "sheets": [], "fields": flat,
                "line_count": max((len(v) for v in rows.values()), default=0),
                "summary": f"{len(flat)} field(s), no workbook — this template submits fields directly."}

    from app.core.excel_entry import _literals, _rows_for_plan, describe, sheet_plans
    from app.models.custom_field import CustomField

    # Where each column's value comes from, so the screen can say so at a glance instead of
    # showing 298 identical-looking cells. Half this workbook is columns nobody mapped, and
    # an unmapped column and a mapped one that came back empty are completely different
    # problems — one is the template, the other is the document.
    origin: dict[str, str] = {}
    for d in (group.documents if group else []) or []:
        for m in d.marks or []:
            origin.setdefault(m.label_name, "document")
    for cf in db.query(CustomField).filter(CustomField.group_id == (group.id if group else "")).all():
        kind = cf.kind or ""
        origin[cf.label_name] = (
            "reference" if kind == "lookup" else "computed" if kind == "ai" else "fixed"
        )

    cfg = (group.excel_config if group else None) or {}
    sheets = []
    for plan in sheet_plans(cfg):
        # EVERY column, in the workbook's own order. _rows_for_plan returns one value per
        # column of the plan, so filtering the headers to the mapped ones while keeping the
        # full row silently shifted every value to a neighbouring header — Supplier_Name
        # showed "0.00" and the supplier's name appeared under Supplier_Country_Code. A
        # preview that lines values up with the wrong labels is worse than none, because it
        # is read and believed. The mapped ones are flagged instead.
        cols = plan["columns"]
        try:
            data = _rows_for_plan(plan, values, rows)
        except Exception:  # noqa: BLE001
            # A preview must never be the thing that breaks the screen. Show the mapping
            # with no data rather than nothing at all, and say so.
            logger.exception("dump preview: could not lay out sheet %s", plan.get("sheet"))
            data = []
        sheets.append({
            "sheet": plan["sheet"] or "Sheet1",
            "scope": plan["scope"],
            "columns": [{"column": c.get("column"), "header": c.get("header"),
                         "field": c.get("field") or "",
                         # Fixed text or a formula the config supplies, rather than anything
                         # read off a document — worth telling apart on screen.
                         "literal": _literals(c) is not None,
                         # document | reference | computed | fixed | none
                         "source": (origin.get((c.get("field") or "").strip())
                                    or ("fixed" if _literals(c) is not None else "none"))}
                        for c in cols],
            "rows": [[("" if v is None else str(v)) for v in r] for r in data[:200]],
            "row_count": len(data),
        })
    return {"mode": "excel", "sheets": sheets, "fields": [],
            "line_count": max((len(v) for v in rows.values()), default=0),
            "summary": describe(cfg)}


@router.get("/jobs/{job_id}/history", response_model=list[JobEventOut])
def job_history(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> list[JobEventOut]:
    """Everything that has happened to this job, oldest first.

    Behind the Last updated column: an operator can see a job is on ERP Entry, but not when it
    arrived or how long it has been sitting there - which is what they actually need in order
    to chase it.
    """
    job = _load_job(db, job_id, scope, user)
    rows = (db.query(JobEvent)
            .filter(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at.asc())
            .all())
    return [JobEventOut(id=r.id, status=r.status, label=describe_status(r.status),
                        stage=r.stage, note=r.note, at=r.created_at) for r in rows]


def _one_per_field(fvs: list) -> list:
    """One value per (custom field, line). Two extractions of the same job leave two.

    A job extracted twice gets a second COMPLETE set of values rather than replacing the first,
    and the ERP Entry screen then drew every box twice - twelve inputs per product line for six
    fields, interleaved in no particular order and impossible to fill. One job had 622 values
    where every other job had 311, which is exactly twice.

    Deduplicated on READ rather than deleted, because both copies are somebody's work: whichever
    carries a value wins, so an operator's typing is never the copy that gets dropped. A field
    genuinely marked on two documents (the part code sits on the invoice AND the packing list)
    keeps one entry per document - those are different readings of the same thing, not a repeat.
    """
    best: dict = {}
    order: list = []
    for fv in fvs:
        # set_index belongs in the key: with three invoices on the job, (set 1, line 3) and
        # (set 2, line 3) are two different products, and without it the second was being
        # dropped as a duplicate of the first.
        key = (fv.custom_field_id, fv.mark_id, fv.template_document_id,
               fv.set_index, fv.row_index)
        if fv.custom_field_id is None:
            # not a custom field - keep every one, the document tells them apart
            order.append(fv)
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = fv
            order.append(fv)
            continue
        filled = (fv.corrected_value or fv.value or fv.extracted_value or "").strip()
        had = (prev.corrected_value or prev.value or prev.extracted_value or "").strip()
        if filled and not had:
            order[order.index(prev)] = fv
            best[key] = fv
    return order


def _build_detail(db: Session, job: Job) -> JobDetailOut:
    group = db.get(TemplateGroup, job.group_id)
    tdoc_by_id = {d.id: d for d in group.documents}
    job_docs = db.query(JobDocument).filter(JobDocument.job_id == job.id).all()
    _tdoc_order = {d.id: i for i, d in enumerate(group.documents)}

    documents = [
        JobDocumentOut(
            id=jd.id,
            template_document_id=jd.template_document_id,
            name=tdoc_by_id[jd.template_document_id].name if jd.template_document_id in tdoc_by_id else "?",
            doc_type=tdoc_by_id[jd.template_document_id].doc_type if jd.template_document_id in tdoc_by_id else "",
            is_uploaded=jd.file_path is not None,
            page_count=jd.page_count,
            file_index=jd.file_index,
            set_index=jd.set_index,
            original_name=jd.original_name,
            extracted_json=jd.extracted_json,
            approved=jd.approved,
            gk2_approved=jd.gk2_approved,
            is_required=(tdoc_by_id[jd.template_document_id].is_required
                        if jd.template_document_id in tdoc_by_id else True),
            updated_at=jd.updated_at,
        )
        # The TEMPLATE's own order, which is deliberately most-authoritative-first — Bill of
        # lading, Invoice, Packing List, Freight Certificate. Sorting by template_document_id
        # ordered them by UUID, so the tabs came out in an order that meant nothing and did
        # not match how anyone works through a shipment.
        for jd in sorted(
            job_docs,
            key=lambda d: (_tdoc_order.get(d.template_document_id, 999), d.file_index),
        )
    ]

    fvs = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    fvs = _one_per_field(fvs)
    from app.models.custom_field import CustomField as _CF

    ask_ids = {
        m.id for m in db.query(FieldMark)
        .join(TemplateDocument, TemplateDocument.id == FieldMark.document_id)
        .filter(TemplateDocument.group_id == group.id, FieldMark.ask_operator.is_(True)).all()
    }
    ask_ids |= {
        c.id for c in db.query(_CF).filter(_CF.group_id == group.id, _CF.ask_operator.is_(True)).all()
    }
    # Fields that answer themselves — from the customer's reference sheet, or by joining other
    # fields this job already has — rather than needing anyone to type or confirm anything. The
    # submit gate lets these through on their own value; every other asked field wants a typed
    # confirmation. Sent to the screen so it applies the same rule — see
    # JobFieldValueOut.self_filled. Also drives which per-row fields show on the per-document
    # Product Detail card (see lookedUpByRow on the frontend) rather than nowhere at all.
    self_fill_ids = {
        c.id for c in db.query(_CF).filter(
            _CF.group_id == group.id, _CF.kind.in_(("lookup", "composite"))).all()
    }
    # A computed field has no mark and no document of its own, so the screens had nowhere to
    # put it and showed it nowhere at all. These say which documents it was told to read and
    # what produced the value, so it can be shown against the documents it came from.
    cf_rows = db.query(_CF).filter(_CF.group_id == group.id).all()
    cf_sources = {c.id: list(c.source_document_ids or []) for c in cf_rows}
    cf_origin = {
        c.id: ("reference" if c.kind == "lookup"
               else "computed" if c.kind in ("ai", "composite") else "fixed")
        for c in cf_rows
    }
    # Picker-pairing config, denormalized onto every value of the field it's set on - see
    # CustomField.paired_custom_field_id's own docstring.
    cf_paired = {c.id: c.paired_custom_field_id for c in cf_rows}
    cf_picker_heading = {c.id: c.picker_heading for c in cf_rows}
    cf_sync_ids = {c.id: list(c.sync_field_ids or []) or None for c in cf_rows}
    # id -> the guidance the Super Admin wrote for the operator
    hints: dict[str, str] = {}
    for m in db.query(FieldMark).join(TemplateDocument, TemplateDocument.id == FieldMark.document_id).filter(
        TemplateDocument.group_id == group.id, FieldMark.ask_operator_hint.isnot(None)
    ).all():
        hints[m.id] = m.ask_operator_hint
    for c in db.query(_CF).filter(_CF.group_id == group.id, _CF.ask_operator_hint.isnot(None)).all():
        hints[c.id] = c.ask_operator_hint
    # Of the asked fields, which ones actually block Submit Entry.
    required_ids = {
        m.id for m in db.query(FieldMark)
        .join(TemplateDocument, TemplateDocument.id == FieldMark.document_id)
        .filter(
            TemplateDocument.group_id == group.id,
            FieldMark.ask_operator.is_(True),
            FieldMark.ask_operator_required.is_(True),
        ).all()
    } | {
        c.id for c in db.query(_CF).filter(
            _CF.group_id == group.id,
            _CF.ask_operator.is_(True),
            _CF.ask_operator_required.is_(True),
        ).all()
    }
    # Where each mark sits on its own page, straight off FieldMark - lets the frontend
    # highlight/scroll to the exact spot on the document preview when a field is focused.
    mark_pos: dict[str, tuple[int, float, float, float, float]] = {
        m.id: (m.page_number, m.x, m.y, m.width, m.height)
        for tdoc in group.documents for m in tdoc.marks
    }
    field_values = []
    for fv in fvs:
        mp = mark_pos.get(fv.mark_id)
        field_values.append(
            JobFieldValueOut(
                id=fv.id,
                custom_field_id=fv.custom_field_id,
                mark_id=fv.mark_id,
                template_document_id=fv.template_document_id,
                document_name=(
                    "✨ Custom"
                    if fv.custom_field_id
                    else (tdoc_by_id[fv.template_document_id].name if fv.template_document_id in tdoc_by_id else "?")
                ),
                label_name=fv.label_name,
                extracted_value=fv.extracted_value,
                corrected_value=fv.corrected_value,
                value=fv.value,
                is_custom=bool(fv.custom_field_id),
                ask_operator=(fv.mark_id in ask_ids) or (fv.custom_field_id in ask_ids),
                ask_operator_required=(fv.mark_id in required_ids) or (fv.custom_field_id in required_ids),
                ask_operator_hint=hints.get(fv.mark_id) or hints.get(fv.custom_field_id),
                self_filled=fv.custom_field_id in self_fill_ids,
                job_document_id=fv.job_document_id,
                set_index=fv.set_index,
                # An AI field with nothing chosen reads EVERY document — the same fallback the
                # extraction takes — so send the full list rather than an empty one the screen
                # would have to interpret.
                source_document_ids=(
                    (cf_sources.get(fv.custom_field_id) or [d.id for d in group.documents])
                    if fv.custom_field_id else []
                ),
                origin=cf_origin.get(fv.custom_field_id, "document"),
                row_index=fv.row_index,
                mark_page=mp[0] if mp else None,
                mark_x=mp[1] if mp else None,
                mark_y=mp[2] if mp else None,
                mark_width=mp[3] if mp else None,
                mark_height=mp[4] if mp else None,
                found_page=fv.found_page,
                found_x=fv.found_x,
                found_y=fv.found_y,
                found_width=fv.found_width,
                found_height=fv.found_height,
                paired_custom_field_id=cf_paired.get(fv.custom_field_id),
                picker_heading=cf_picker_heading.get(fv.custom_field_id),
                sync_field_ids=cf_sync_ids.get(fv.custom_field_id),
            )
        )

    doc_name_by_mark: dict[str, str] = {}
    for tdoc in group.documents:
        for mark in tdoc.marks:
            doc_name_by_mark[mark.id] = tdoc.name

    links = db.query(CrossDocLink).filter(CrossDocLink.group_id == group.id).all()
    findings = _verification_findings(db, job, links)
    # Only worth labelling the sets when there is more than one; a single-invoice job reads
    # exactly as it always did.
    many = len({f["set_index"] for f in findings}) > 1
    # A link's source label/document: a mark's are read off FieldMark/doc_name_by_mark as
    # before; a custom field has a label but no single document (it can read several, or all
    # of them) - "Custom Field" says plainly where the value actually came from rather than
    # a document name that would be a guess.
    custom_field_ids = {link.source_custom_field_id for link in links if link.source_mark_id is None}
    custom_field_labels: dict[str, str] = {}
    if custom_field_ids:
        from app.models.custom_field import CustomField

        custom_field_labels = {
            c.id: c.label_name
            for c in db.query(CustomField).filter(CustomField.id.in_(custom_field_ids)).all()
        }
    verifications: list[VerificationRow] = []
    for f in findings:
        link = f["link"]
        tag = f" — invoice {f['set_index']}" if many else ""
        if link.source_mark_id is not None:
            src_mark = db.get(FieldMark, link.source_mark_id)
            src_label = src_mark.label_name if src_mark else "?"
            src_doc = doc_name_by_mark.get(link.source_mark_id, "?")
        else:
            src_label = custom_field_labels.get(link.source_custom_field_id, "?")
            src_doc = "Custom Field"
        verifications.append(
            VerificationRow(
                link_id=f["id"],
                field_label=src_label + tag,
                source_document=src_doc,
                source_value=f["source_value"],
                target_document=doc_name_by_mark.get(link.target_mark_id, "?"),
                target_value=f["target_value"],
                status=f["status"],
                accepted=f["accepted"],
            )
        )
    for f in _self_verifying_findings(db, job):
        cf = f["custom_field"]
        verifications.append(
            VerificationRow(
                link_id=f["id"],
                field_label=cf.label_name,
                source_document="Custom Field",
                source_value=f["value"],
                # No single other document to name - this field's own prompt already read
                # several documents and reconciled them itself (see _self_verifying_findings).
                target_document="",
                target_value=None,
                status=f["status"],
                accepted=f["accepted"],
            )
        )

    # Passes when nothing genuinely disagrees. A "missing" row is informational — the value is
    # absent on the other document, not in conflict with it — so it does not withhold the pass.
    all_passed = len(verifications) > 0 and all(
        v.status not in BLOCKING_VERIFICATION_STATUSES or v.accepted for v in verifications
    )
    return JobDetailOut(
        id=job.id,
        tenant_id=job.tenant_id,
        group_id=job.group_id,
        group_name=group.name,
        reference=job.reference,
        status=job.status,
        stage=_job_stage(db, job),
        outer_status=_outer_status(db, job),
        mode=_shipment_mode(db, job),
        assigned_operator_id=job.assigned_operator_id,
        operator_name=_operator_name(db, job),
        excel_entry=(group.entry_mode or "fields") == "excel",
        gk2_status=job.gk2_status,
        validation_approved=job.validation_approved,
        gk2_validation_approved=job.gk2_validation_approved,
        irn_documents_done=job.irn_documents_done,
        irn_approval_requested=job.irn_approval_requested,
        needs_reextraction=job.needs_reextraction,
        duplicate_of_job_id=job.duplicate_of_job_id,
        duplicate_of_reference=_duplicate_of_reference(db, job),
        eta_date=job.eta_date,
        documents=documents,
        field_values=field_values,
        verifications=verifications,
        all_checks_passed=all_passed,
        extracted_keyouted_data=job.extracted_keyouted_data,
        # The stored record of the last ERP run. Returned on every read, not only on the
        # response to Submit — otherwise a reload loses the rejected field and the operator
        # has nothing to correct.
        erp_status=job.erp_status,
        erp_reason=job.erp_reason,
        erp_diagnosis=job.erp_diagnosis,
        erp_final_url=job.erp_final_url,
        erp_failed_field=job.erp_failed_field,
        erp_failed_value=job.erp_failed_value,
        erp_log=job.erp_log,
        erp_captured=job.erp_captured,
    )


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> JobDetailOut:
    return _build_detail(db, _load_job(db, job_id, scope, user))


@router.get("/jobs/{job_id}/documents/{job_document_id}/pages/{page_number}")
def get_job_document_page(
    job_id: str,
    job_document_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    """Serve a rendered page image of an uploaded job document (for the preview)."""
    job = _load_job(db, job_id, scope, user)
    jd = db.query(JobDocument).filter(JobDocument.id == job_document_id, JobDocument.job_id == job.id).first()
    if jd is None or jd.file_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job document not found")
    if page_number < 1 or page_number > jd.page_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page out of range")
    image = _job_doc_dir(jd.id) / "pages" / f"page_{page_number}.png"
    if not image.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered page missing")
    return FileResponse(image, media_type="image/png")


@router.get("/jobs/{job_id}/captured/{file_name}")
def get_captured_file(
    job_id: str,
    file_name: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    """Download a document the ERP produced during entry — a filed Bill of Entry, a challan.

    Named by the operator's own label in the UI, but fetched by stored filename. The name is
    resolved against what the run actually recorded rather than trusted from the URL, so a
    crafted path cannot reach outside this job's folder.
    """
    job = _load_job(db, job_id, scope, user)
    allowed = {
        item.get("file")
        for item in (job.erp_captured or {}).values()
        if isinstance(item, dict) and item.get("file")
    }
    if file_name not in allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such captured file")
    path = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id / file_name
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Captured file missing on disk")
    return FileResponse(path, filename=file_name)


@router.get("/jobs/{job_id}/erp-debug-shots")
def get_erp_debug_shots(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """The screen an AI takeover decided from, for every AI-driven click a run had to make
    (see browser.py's ai_takeover) - diagnostic only, never shown to an operator. The final
    failure screenshot (erp_screenshot) only ever shows where a run gave up; this is what it
    was actually looking at each time the recorded script lost its place and the AI had to
    guess, which the text log alone cannot show."""
    import base64

    job = _load_job(db, job_id, scope, user)
    shot_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
    if not shot_dir.is_dir():
        return {"shots": []}
    shots = []
    for path in sorted(shot_dir.glob("ai-takeover-*.png")):
        shots.append({
            "name": path.name,
            "data_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
        })
    return {"shots": shots}


class RulingRequest(BaseModel):
    answer: str | None = None


def _document_records(db: Session, job: Job, group: TemplateGroup) -> list[dict]:
    """One compact JSON record per uploaded file — the structured reading of this job.

    This is the middle of a map-reduce. Each document is read in full ONCE, on its own, and
    becomes a record; anything that has to reason across ALL the documents then reads the
    records instead of the paper. Twenty invoices are ~49,000 characters of raw text and only
    81% of that fits the budget — the missing fifth being each document's tail, which is where
    a total sits. The same twenty as records are ~16,500 characters, complete.

    ONE implementation, shared by extraction's computed fields and the ruling, so the two
    cannot drift into judging a job on different readings of it.
    """
    docs_by_tdoc: dict[str, list[JobDocument]] = {}
    for d in (db.query(JobDocument)
              .filter(JobDocument.job_id == job.id)
              .order_by(JobDocument.file_index).all()):
        docs_by_tdoc.setdefault(d.template_document_id, []).append(d)

    per_doc: dict[str, dict] = {}
    for fv in (db.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id,
                       JobFieldValue.custom_field_id.is_(None),
                       JobFieldValue.job_document_id.isnot(None)).all()):
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if not val:
            continue
        rec = per_doc.setdefault(fv.job_document_id, {"fields": {}, "lines": {}})
        if fv.row_index is None:
            rec["fields"].setdefault(fv.label_name, val)
        else:
            rec["lines"].setdefault(fv.row_index, {})[fv.label_name] = val

    records: list[dict] = []
    for tdoc in group.documents:
        for d in docs_by_tdoc.get(tdoc.id, []):
            if not d.file_path:
                continue
            r = per_doc.get(d.id) or {"fields": {}, "lines": {}}
            records.append({
                "document": tdoc.name,
                "file": d.original_name or f"{tdoc.name} #{d.file_index + 1}",
                # Which invoice this belongs to, so a total can be taken per set or overall
                # and a packing list can be matched to the invoice it goes with.
                "set": d.set_index,
                "template_document_id": tdoc.id,
                "fields": r["fields"],
                "lines": [r["lines"][k] for k in sorted(r["lines"])],
            })
    return records


def _ruling_context(db: Session, job: Job, group: TemplateGroup) -> tuple[list[str], list[str], str]:
    """Build the text handed to the ruling: (all document names, uploaded names, fenced text).

    Each document's OCR is fenced under its own name. Without that the pages arrive as one
    undifferentiated blob and the AI can only *infer* which text is the BL from its
    letterhead — so a rule like "look in the BL first, then the Invoice" is a hope rather
    than an instruction, and a mis-OCR'd header silently misroutes it.
    """
    all_names = [d.name for d in group.documents]
    # Every file in every slot — a job with three invoices must have all three put in front
    # of the ruling, or a rule like "hold if any invoice is missing its incoterm" is deciding
    # on a third of the evidence.
    docs_by_tdoc: dict[str, list[JobDocument]] = {}
    for _jd in (db.query(JobDocument)
                .filter(JobDocument.job_id == job.id)
                .order_by(JobDocument.file_index).all()):
        docs_by_tdoc.setdefault(_jd.template_document_id, []).append(_jd)

    custom_filter_texts = get_active_custom_filter_texts(db)
    parts: list[str] = []
    uploaded: list[str] = []
    raw_by_doc: list[tuple] = []
    for tdoc, jd in [(t, d) for t in group.documents for d in docs_by_tdoc.get(t.id, [])]:
        if jd.file_path is None:
            continue
        several = len([d for d in docs_by_tdoc.get(tdoc.id, []) if d.file_path]) > 1
        label = f"{tdoc.name} #{jd.file_index + 1}" if several else tdoc.name
        pages: list[str] = []
        for page in range(1, jd.page_count + 1):
            try:
                pages.append(get_page_ocr(_job_doc_dir(jd.id), page).get("text", ""))
            except Exception:  # noqa: BLE001
                pass
        if not any(p.strip() for p in pages):
            continue
        if tdoc.name not in uploaded:
            uploaded.append(tdoc.name)
        # Same terms-page filter as extraction: the ruling's whole job is to find the incoterm,
        # and 39,572 characters of carrier liability clauses is exactly what buried it.
        kept, _dropped = filter_pages_with_custom(pages, custom_filter_texts, tdoc.name)
        raw_by_doc.append((tdoc, "\n".join(kept), label))

    # Give every document an equal share of the budget rather than letting the first one take
    # what it likes. A bill of lading runs to 42,000 characters — 39,000 of them page 2 carrier
    # boilerplate — so fenced first it consumed the whole allowance and the Invoice and Packing
    # List never reached the model at all: the ruling could not see "FCA Suzhou" and fell back
    # to the BL's freight terms. Dividing by the document count keeps that true for a template
    # with eight documents, not just this one's four. Trade terms sit near the top of each.
    if raw_by_doc:
        share = max(2000, RULING_TEXT_BUDGET // len(raw_by_doc))
        for tdoc, text, label in raw_by_doc:
            if len(text) > share:
                text = text[:share] + "\n…[document truncated for the ruling]"
            parts.append(f"=== DOCUMENT: {label} ({tdoc.doc_type}) ===\n" + text)

    # Telling it what is still missing is what lets a rule answer "upload these next".
    missing = [n for n in all_names if n not in uploaded]

    # Incoterms are a CLOSED vocabulary, so find them by exact text search rather than hoping
    # the model spots them: with "Delivery Terms: FCA Suzhou" sitting in the Invoice it still
    # answered "no real incoterm found" and held the job. The search only reports what is
    # printed and where — the admin's rule still decides what that means and which documents
    # it requires, so the business logic stays configuration, not code.
    found_lines: list[str] = []
    for tdoc, text, label in raw_by_doc:
        hits: list[str] = []
        for m in INCOTERM_RE.finditer(text):
            token = m.group(0).upper()
            if token not in hits:
                hits.append(token)
        if hits:
            first = INCOTERM_RE.search(text)
            snippet = " ".join(text[max(0, first.start() - 40): first.start() + 30].split())
            found_lines.append(f"  {label}: {', '.join(hits)}   (printed as: …{snippet}…)")
        else:
            found_lines.append(f"  {label}: none")

    # The structured reading of every file, complete and never shortened. The raw text below
    # it is divided among the documents and IS shortened, so on a job carrying twenty invoices
    # a rule that asks about "all the invoices" must answer from these records — the raw text
    # can only show it about four fifths of them.
    records = _document_records(db, job, group)
    records_block = ""
    if records:
        records_block = (
            f"Structured reading of every uploaded file ({len(records)} record(s) — this is "
            "the COMPLETE set, nothing omitted). A job can carry several invoices with their\n"
            "own packing lists, paired by \"set\". Answer from these when the rule concerns\n"
            "values, totals or counts across the documents:\n"
            + json.dumps(records, ensure_ascii=False) + "\n\n"
        )

    docs_text = (
        f"Documents uploaded so far: {uploaded or 'none'}\n"
        f"Documents not yet uploaded: {missing or 'none'}\n\n"
        "Incoterm codes found by exact text search, per document — treat these as reliably\n"
        "present. Apply the rule to them; do not conclude that no incoterm exists when this\n"
        "list shows one:\n" + "\n".join(found_lines) + "\n\n"
        + records_block
        + "Raw document text below is supporting evidence and may be shortened:\n"
        + "\n\n".join(parts)
    )
    return all_names, uploaded, docs_text


def apply_ruling_hold(db: Session, job: Job) -> None:
    """Run the template's ruling and hold the job if it needs the operator to decide.

    Called automatically after extraction so a job holds itself — the operator should not
    have to press a button to discover the incoterm could not be determined. Best-effort:
    a ruling failure must never break extraction.
    """
    from app.core.llm import evaluate_ruling

    group = db.get(TemplateGroup, job.group_id)
    if group is None or not group.ruling_prompt:
        return
    try:
        all_names, _uploaded, docs_text = _ruling_context(db, job, group)
        result = evaluate_ruling(group.ruling_prompt, all_names, docs_text, None)
    except Exception:  # noqa: BLE001
        logger.exception("auto-ruling failed for job %s", job.id)
        return
    if result.get("needs_input"):
        job.stage_override = HOLD_STAGE
        db.commit()
        logger.info("job %s held: %s", job.id, result.get("question"))


@router.post("/jobs/{job_id}/ruling")
def evaluate_job_ruling(
    job_id: str,
    req: RulingRequest,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    """Apply the template's custom ruling to this job's uploaded documents and return
    which documents are required (and, if the AI can't decide, a question for the operator).
    Optionally pass the operator's `answer` to resolve an earlier question."""
    from app.core.llm import evaluate_ruling

    job = _load_job(db, job_id, scope, user)
    group = db.get(TemplateGroup, job.group_id)
    if not group.ruling_prompt:
        return {"has_ruling": False}

    all_names, uploaded, docs_text = _ruling_context(db, job, group)
    result = evaluate_ruling(group.ruling_prompt, all_names, docs_text, req.answer)
    result["has_ruling"] = True
    result["all_documents"] = all_names
    result["uploaded_documents"] = uploaded
    result["missing_documents"] = [
        n for n in (result.get("required_documents") or []) if n not in uploaded
    ]

    # The rule could not determine the deciding value (e.g. no incoterm in the BL, the
    # Invoice or the Packing List) and the operator has not answered yet — hold the job so
    # it cannot be pushed to the ERP on a guess. stage_override is what the operator's
    # stage badge reads, and rerun_job already clears it.
    if result["needs_input"] and not (req.answer or "").strip():
        job.stage_override = HOLD_STAGE
        db.commit()
    elif job.stage_override == HOLD_STAGE:
        job.stage_override = None
        db.commit()
    result["held"] = job.stage_override == HOLD_STAGE
    return result


@router.post("/jobs/{job_id}/rerun", response_model=JobDetailOut)
def rerun_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> JobDetailOut:
    """Clear THIS job's failed run and stand it ready to enter again. Nothing is lost.

    This used to restart the job from the first stage: it DELETED every extracted value, every
    verification approval, and set the status back to draft. So the ordinary case - an entry
    that failed on one field, the operator corrects that field on the screen and wants to try
    again - threw away all the other work, including values that had been typed in by hand. The
    four duty notifications on this customer's jobs were retyped four times in one day for
    exactly that reason.

    A re-run is not a restart. Everything the operator has entered stays exactly as it is; only
    the record of the last run is cleared, and only for this job. To rebuild the values from
    the documents there is a separate, deliberate action: re-run extraction.
    """
    job = _load_job(db, job_id, scope, user)
    _put_back_on_entry(db, job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/verification-decision", response_model=JobDetailOut)
def set_verification_decision(
    job_id: str,
    payload: VerificationDecision,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> JobDetailOut:
    """Operator accepts (or un-accepts) a flagged cross-verification row."""
    job = _load_job(db, job_id, scope, user)
    accepted = set(job.accepted_verifications or [])
    if payload.accept:
        accepted.add(payload.link_id)
    else:
        accepted.discard(payload.link_id)
    job.accepted_verifications = sorted(accepted)
    db.commit()
    db.refresh(job)
    return _build_detail(db, job)



def entry_values_and_rows(db: Session, job: Job) -> tuple[dict, dict]:
    """(values, rows) for an ERP run: job-level fields, and one value per LINE.

    Shared with the Entry Browser, which had its own one-liner:

        values = {fv.label_name: fv.value for fv in ...}

    Keyed by label alone, all fourteen lines of a Product CTH collapse into whichever the
    database returned last - so a replay entered one line's code against every product and
    looked like it had worked. The job runner learnt that lesson already; there is no second
    version of it now.
    """
    from app.core.excel_entry import LINE_SET_KEY, SET_VALUES_KEY

    job_fvs = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    # A line-item field is often marked on TWO documents so the two can be cross-checked.
    # Collapse on the LINE, keeping the first non-empty value — and a line is (set, row),
    # not row alone: with three invoices on one job "line 1" is three different products,
    # and collapsing on the row number kept one and dropped the other two.
    _by_line: dict[str, dict[tuple, str]] = {}
    for fv in job_fvs:
        if fv.row_index is not None:
            key = ((fv.set_index or 1), fv.row_index)
            slot = _by_line.setdefault(fv.label_name, {})
            if not (slot.get(key) or "").strip():
                slot[key] = fv.value or ""
    # Every line on the job, invoice by invoice and in line order within each. A field the
    # packing list carries but the invoice does not still lines up, because every column is
    # laid out against the SAME list of lines rather than against its own.
    line_keys = sorted({k for slots in _by_line.values() for k in slots})
    rows: dict[str, list[str]] = {
        label: [slots.get(k, "") for k in line_keys] for label, slots in _by_line.items()
    }
    if line_keys:
        # Which invoice each line belongs to, in the same order. Read by excel_entry to fill
        # the invoice serial, so a product stays attached to the invoice it was billed on.
        rows[LINE_SET_KEY] = [str(k[0]) for k in line_keys]

    # Job-level fields: first NON-EMPTY wins, whole-job documents first, then invoice 1.
    # Keying by label alone let whichever row the database returned last decide, so with
    # three invoices the header fields came from an arbitrary one of them.
    values: dict[str, str] = {}
    for fv in sorted(job_fvs, key=lambda f: (f.set_index or 0, f.id)):
        if fv.row_index is not None:
            continue
        v = fv.value or ""
        if v.strip() or fv.label_name not in values:
            values[fv.label_name] = v
    for label, vals in rows.items():
        if label in (LINE_SET_KEY, SET_VALUES_KEY):
            continue
        values.setdefault(label, vals[0] if vals else "")
    values.setdefault("job_reference", job.reference or "")

    # Each invoice's OWN job-level readings, for a sheet scoped one-row-per-invoice. Only
    # attached when the job really has several, so a single-invoice job produces exactly the
    # workbook it produced before this change.
    per_set: dict[int, dict[str, str]] = {}
    for fv in job_fvs:
        if fv.row_index is None and fv.set_index:
            d = per_set.setdefault(fv.set_index, {})
            v = fv.value or ""
            if v.strip() or fv.label_name not in d:
                d[fv.label_name] = v
    if len(per_set) > 1:
        rows[SET_VALUES_KEY] = [per_set[k] for k in sorted(per_set)]
    return values, rows


class ExcelBuildFailed(Exception):
    """Raised by _build_job_excel when the workbook could not be built, or built empty - an
    operator-readable reason, never a bare exception."""


def _build_job_excel(db: Session, job: Job, out_dir=None) -> Path:
    """Build this job's ERP-import workbook fresh from its CURRENT extracted values.

    The same build complete_job's Excel-entry path already uses (see entry_uploads below),
    factored out so GK2's final approval and the "Download as Excel" button go through the
    identical logic rather than two copies that could drift apart. Written to its own
    `job_excel/{job.id}/` folder by default so a fresh download always reflects the job's
    latest data rather than serving a stale copy from wherever a browser-automation run last
    happened to write one.

    AN EMPTY WORKBOOK MUST NOT REACH THE ERP (see complete_job) - refused here BEFORE the
    build, not after, since a genuinely empty result is a decided outcome, not a build error.
    """
    from app.core.excel_entry import build_for_job

    grp = db.get(TemplateGroup, job.group_id)
    if grp is None or (grp.entry_mode or "fields") != "excel":
        raise ExcelBuildFailed("This job's template is not set up for Excel entry.")
    cfg = grp.excel_config or {}
    values, rows = entry_values_and_rows(db, job)
    filled, mapped = _column_fill(cfg, values, rows)
    if mapped and not filled:
        raise ExcelBuildFailed(
            f"Every one of the {mapped} mapped columns is empty, so the import spreadsheet "
            "would have no data in it. Check the job's extracted values before trying again."
        )
    tpl_dir = Path(get_settings().uploads_dir) / "excel_templates" / grp.id
    tpl = (next((p for p in tpl_dir.glob("template.*")), None)
          if cfg.get("source") == "template" else None)
    target_dir = Path(out_dir) if out_dir else Path(get_settings().uploads_dir) / "job_excel" / job.id
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        return build_for_job(cfg, values, rows, target_dir, tpl, name_as=job.reference or job.id)
    except ExcelBuildFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExcelBuildFailed(f"The import spreadsheet could not be built: {exc}") from exc


def entry_uploads(db: Session, job: Job, values: dict, rows: dict, out_dir) -> dict:
    """{name: path on disk} for every `upload` step - the operator's documents AND, for an
    Excel-entry customer, the workbook built from THIS job.

    The Entry Browser passed nothing at all, so `excel_import` did not exist, step 20 had no
    file to attach, the ERP's import popup stayed open with "No file chosen", and every step
    after it failed against a dimmer it could not get past. The workbook was never the problem
    - nobody was building it for that path.
    """
    from pathlib import Path as _P

    up_map: dict[str, str] = {}
    for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
        if not jd.file_path:
            continue
        tdoc = (db.get(TemplateDocument, jd.template_document_id)
                if jd.template_document_id else None)
        for key in filter(None, (tdoc.name if tdoc else None, _P(jd.file_path).name)):
            up_map.setdefault(str(key), str(jd.file_path))
    grp = db.get(TemplateGroup, job.group_id)
    if grp is not None and (grp.entry_mode or "fields") == "excel":
        from app.core.excel_entry import build_for_job

        cfg = grp.excel_config or {}
        tpl_dir = _P(get_settings().uploads_dir) / "excel_templates" / grp.id
        tpl = (next((x for x in tpl_dir.glob("template.*")), None)
               if cfg.get("source") == "template" else None)
        book = build_for_job(cfg, values, rows, _P(out_dir), tpl,
                             name_as=job.reference or job.id)
        up_map.setdefault("excel_import", str(book))
        up_map.setdefault(book.name, str(book))
        # The file is now named after the job, but a step recorded before that still asks for
        # the TEMPLATE's file name. Keep answering to it, or every existing recording stops
        # finding its own workbook the moment this ships.
        if cfg.get("file_name"):
            up_map.setdefault(str(cfg["file_name"]), str(book))
    return up_map


def _unanswered_operator_fields(db: Session, job: Job) -> list[str]:
    """Labels of ask_operator fields this job still needs the operator to confirm."""
    from app.models.custom_field import CustomField

    # Flagged fields come from two places: boxes drawn on a document, and custom tags that
    # appear on no document at all (the insurance percentage). Both must be checked — an
    # early return here would silently skip the second kind.
    # Only MANDATORY asked fields block the job. An optional one is still shown to the
    # operator and still asked for, but a value they genuinely do not have yet — an IGM
    # number the shipping line has not issued — must never hold the entry hostage.
    asked_marks = {
        m.id: m.label_name
        for m in db.query(FieldMark)
        .join(TemplateDocument, TemplateDocument.id == FieldMark.document_id)
        .filter(
            TemplateDocument.group_id == job.group_id,
            FieldMark.ask_operator.is_(True),
            FieldMark.ask_operator_required.is_(True),
        )
        .all()
    }
    asked_custom_rows = db.query(CustomField).filter(
        CustomField.group_id == job.group_id,
        CustomField.ask_operator.is_(True),
        CustomField.ask_operator_required.is_(True),
    ).all()
    asked_customs = {c.id: c.label_name for c in asked_custom_rows if not getattr(c, "per_row", False)}
    # A per-row field is not one question but N. Line 7 being filled says nothing about line 8,
    # so each slot is checked on its own and named individually when it is missing.
    per_row_ids = {c.id: c.label_name for c in asked_custom_rows if getattr(c, "per_row", False)}
    # A LOOKUP field answers itself. Its value comes from the customer's own reference sheet,
    # which is more authoritative than anything an operator would type - so requiring them to
    # retype it to "confirm" it is busywork that blocks the entry. It held up this very job:
    # the screen showed the CTH and RITC on all fourteen lines, filled from the dump, while
    # Submit refused because no CORRECTED value had been saved on them.
    self_filling = {c.id for c in asked_custom_rows if getattr(c, "kind", "") == "lookup"}
    if not asked_marks and not asked_customs and not per_row_ids:
        return []

    # A field counts as answered once the operator has saved a corrected value on it.
    corrected = db.query(JobFieldValue).filter(
        JobFieldValue.job_id == job.id, JobFieldValue.corrected_value.isnot(None)
    ).all()
    answered_marks = {fv.mark_id for fv in corrected if fv.mark_id}
    answered_customs = {fv.custom_field_id for fv in corrected if fv.custom_field_id}
    # ... and a job-level lookup counts the same way
    for fv in db.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.custom_field_id.in_(list(self_filling) or [""])).all():
        if (fv.value or fv.extracted_value or "").strip():
            answered_customs.add(fv.custom_field_id)

    unanswered_rows: list[str] = []
    if per_row_ids:
        for fv in db.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.custom_field_id.in_(list(per_row_ids)),
            JobFieldValue.row_index.isnot(None),
        ).order_by(JobFieldValue.row_index).all():
            answered = (fv.corrected_value or "").strip()
            if not answered and fv.custom_field_id in self_filling:
                # filled from the reference sheet - that IS the answer
                answered = (fv.value or fv.extracted_value or "").strip()
            if not answered:
                unanswered_rows.append(f"{per_row_ids[fv.custom_field_id]} (line {fv.row_index})")

    return (
        [label for mid, label in asked_marks.items() if mid not in answered_marks]
        + [label for cid, label in asked_customs.items() if cid not in answered_customs]
        + unanswered_rows
    )


def persist_run_outcome(db: Session, job: Job, script, result: dict,
                        operator_id: str | None = None) -> dict:
    """Write down what an ERP run did, and decide the job's status from it.

    ONE implementation, because there are two ways to run an entry and they must leave the job
    in the same state. Submit went through this; the Entry Browser's live rerun went through
    nothing at all - it drove the whole entry, watched it finish, and wrote NOTHING back. So a
    rerun could complete all thirty steps, render the ERP's checklist on screen, and the job
    still read "failed" on both the admin and the operator screen, with no log, no captured
    reference, nothing for the operator to look at. Anything that runs an entry calls this.

    Returns the `erp_*` fields to hang on the response; the caller refreshes and builds its own
    detail, because the two callers return different shapes.
    """
    # The reference the ERP generated (Bill of Entry number / acknowledgement) is the only
    # durable proof the entry happened on their side. Kept even on a failed or duplicate
    # outcome - a run that got far enough to read it is exactly when it is needed.
    if result.get("captured"):
        job.erp_captured = {k: v for k, v in result["captured"].items() if v}
    # A picture of where a FAILED run actually stopped, persisted the same way a successful
    # run's own screenshot already is (captured["erp_success_screenshot"], written to disk
    # by browser.py itself) - without this, the screen a failure happened on only ever
    # existed in the single response that fired the run. The operator sees a text reason
    # ("stuck too long", "Invalid Data Found") on every later visit, with no way to tell
    # WHAT was actually on screen when it stopped, unlike a successful run's own
    # screenshot, which survives every reload. Skipped for a clean "ok" outcome - that one
    # already gets its own picture from browser.py, and writing a second, redundant file
    # for every successful run would be pure waste.
    if result.get("screenshot") and result.get("status") != "ok":
        import base64

        shot_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
        try:
            shot_dir.mkdir(parents=True, exist_ok=True)
            png_path = shot_dir / "erp-failure.png"
            png_path.write_bytes(base64.b64decode(result["screenshot"]))
            job.erp_captured = {
                **(job.erp_captured or {}),
                "erp_failure_screenshot": {
                    "label": "Where the ERP entry stopped",
                    "value": "",
                    "kind": "image",
                    "description": "What the ERP was showing when the entry failed.",
                    "file": png_path.name,
                    "usage": "both",
                },
            }
        except Exception:  # noqa: BLE001 — a picture must never fail a run
            logger.exception("could not save the failure screenshot for job %s", job.id)
    # PERSIST the run, don't just return it. The operator needs the rejected field to survive a
    # page reload: that is what gets highlighted in red so they can correct it and re-run.
    # Everything except the screenshot is stored (a base64 PNG per run per job would bloat the
    # table; it is still returned for the immediate view) - the image itself now is too, just
    # as a file rather than inline, exactly like the success screenshot already was.
    job.erp_status = result.get("status")
    job.erp_reason = result.get("reason")
    job.erp_diagnosis = result.get("ai_diagnosis")
    job.erp_final_url = result.get("final_url")
    job.erp_failed_field = result.get("failed_field")
    job.erp_failed_value = result.get("failed_value")
    job.erp_log = result.get("log")
    db.commit()

    erp = {
        "erp_status": job.erp_status,
        "erp_captured": job.erp_captured,
        "erp_final_url": job.erp_final_url,
        "erp_log": job.erp_log,
        "erp_screenshot": result.get("screenshot"),
        "erp_reason": job.erp_reason,
        "erp_failed_field": job.erp_failed_field,
        "erp_failed_value": job.erp_failed_value,
        "erp_diagnosis": job.erp_diagnosis,
    }
    status = result.get("status")

    def _report(reason: str, details: str) -> None:
        from app.models.job_failure import JobFailureReport

        db.add(JobFailureReport(
            tenant_id=job.tenant_id, job_id=job.id,
            operator_id=operator_id or job.assigned_operator_id,
            job_reference=job.reference,
            erp_url=getattr(script, "url", "") or "",
            reason=reason, details=details[:4000], status="open"))

    if status == "duplicated":
        # The form locked after entry - the record already exists. Not a failure.
        job.status = "duplicate"
        job.stage_override = None
        db.commit()
        return erp
    if status in ("error", "failed"):
        # Don't complete. Raise a report to the Super Admin inbox and surface it to the operator.
        failed_steps = result.get("failed_steps") or []
        parts = []
        if failed_steps:
            parts.append("Failed steps: " + "; ".join(str(s) for s in failed_steps))
        parts.append("Log:")
        parts += [str(x) for x in (result.get("log") or [])]
        _report(result.get("reason") or "The web-entry could not be completed.",
                "\n".join(parts))
        job.status = "failed"
        job.stage_override = None
        db.commit()
        return erp
    if status == "partial":
        # The entry WENT IN - the final action ran - but some steps failed on the way, so the
        # record may be missing a date, a document or a captured value. Completing it silently
        # is how those reached the ERP unnoticed; marking it failed would invite a re-run and a
        # duplicate record. So: complete the job, and still raise a report so somebody looks.
        lines_ = ["The entry was submitted but these steps failed:"]
        lines_ += [str(x) for x in (result.get("failed_steps") or [])]
        lines_ += ["", "Log:"]
        lines_ += [str(x) for x in (result.get("log") or [])]
        _report(result.get("reason") or "The entry completed with failed steps.",
                "\n".join(lines_))

    job.status = "completed"
    # An override says where a job is WAITING. It has just finished, so there is nothing left
    # to wait on - and leaving it behind is what made a completed job read "ERP Entry".
    job.stage_override = None
    db.commit()
    return erp



@router.post("/jobs/{job_id}/complete", response_model=JobDetailOut)
def complete_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Submit Entry: replay the template's ERP script into the real ERP with this job's
    extracted values, then mark the job completed on success."""
    from app.core.browser import play_steps
    from app.models.erp_script import ErpScript

    job = _load_job(db, job_id, scope, user)
    if job.status != "extracted":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run extraction before submitting the entry.")
    # Fields the Super Admin ticked as "ask the operator" must be answered on THIS job
    # before anything is entered. A field counts as answered once the operator has set a
    # corrected value on it — confirming it is an explicit act, not a default.
    pending = _unanswered_operator_fields(db, job)
    if pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Enter a value for these fields before submitting the entry: "
                    + ", ".join(pending)),
        )
    # Held on the ruling's question — entering this into the ERP would be acting on a guess.
    if job.stage_override == HOLD_STAGE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is on hold — answer the required-documents question before submitting the entry.",
        )

    # Find the ready ERP script that covers this job's template.
    script = next(
        (
            s
            for s in db.query(ErpScript).filter(ErpScript.tenant_id == job.tenant_id, ErpScript.status == "ready").all()
            if job.group_id in (s.template_ids or [])
        ),
        None,
    )

    erp: dict = {}
    # Bound here, not only inside the else below: the code after the branch reads it, and on
    # the no-script path it was never assigned at all.
    result: dict = {}
    if script is None:
        erp = {"erp_status": "no_script", "erp_log": ["No ready ERP script is configured for this template."]}
        # Nothing was entered anywhere, but the operator's work on the job IS finished.
        job.status = "completed"
        db.commit()
    else:
        # The same inputs the Entry Browser's replay gets - one function, so a fix to how
        # line items are collapsed can never land on only one of the two paths.
        values, rows = entry_values_and_rows(db, job)
        login = (
            {"username": script.login_username or "", "password": script.login_password or ""}
            if script.has_login
            else None
        )
        # Mark it Running so the list/dashboard shows live progress while the background
        # ERP entry is in flight (visible to a concurrent viewer / after this returns).
        job.status = "processing"
        db.commit()
        # Always run in the background (headless) — no OS browser window pops up. The
        # Super Admin Entry Browser streams the same run into the app panel to watch/edit.
        try:
            # Documents the ERP hands back (a filed Bill of Entry PDF, a challan) are saved
            # under the job, so they sit beside the documents the operator uploaded.
            dl_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
            dl_dir.mkdir(parents=True, exist_ok=True)
            # A script whose Super Admin marked a checkpoint keeps the logged-in session it
            # reached there, so the next job jumps straight to the entry screen instead of
            # replaying the login. One session file per script; only read when the script
            # still has a checkpoint AND has "stay open" switched on.
            sess_file = None
            ckpt = script.checkpoint_index if script.stay_open else None
            if ckpt is not None:
                sess_file = (
                    Path(get_settings().uploads_dir) / "erp_sessions" / f"{script.id}.json"
                )
            result = None
            # A parked session cannot carry an attachment: parked.py calls play_steps without
            # `uploads`, so an upload step there has nothing to resolve. An Excel-entry job is
            # ONE attachment, so it must take the ordinary path or it would submit an import
            # with no file. (The same gap affects document uploads on parked sessions - a
            # pre-existing limitation, not introduced here.)
            grp_for_mode = db.get(TemplateGroup, job.group_id) if job.group_id else None
            needs_attachment = (
                grp_for_mode is not None and (grp_for_mode.entry_mode or "fields") == "excel"
            )
            if ckpt is not None and not needs_attachment:
                # A browser may already be parked at this script's checkpoint — logged in and
                # sitting on the entry screen. Hand it the job: it runs only the steps below
                # the checkpoint, then closes and re-parks itself. If nothing is parked, or the
                # parked session is busy or wedged, this returns None and we run normally.
                from app.core.parked import MANAGER

                result = MANAGER.run_job(script.id, values, rows, job.reference or job.id)
                if result is not None:
                    logger.info("job %s ran on the parked session for script %s", job.id, script.id)
            if result is None:
                # Documents the operator uploaded, so an `upload` step can attach one by NAME.
                # A script must never carry a machine path: recorded on one server it has to
                # run on another, where the file sits somewhere different.
                up_map: dict[str, str] = {}
                for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
                    if not jd.file_path:
                        continue
                    tdoc = (db.get(TemplateDocument, jd.template_document_id)
                            if jd.template_document_id else None)
                    for key in filter(None, (tdoc.name if tdoc else None, Path(jd.file_path).name)):
                        up_map.setdefault(str(key), str(jd.file_path))
                # Excel entry: this customer's ERP takes a bulk import rather than fifty typed
                # boxes, so the job becomes ONE workbook and the recorded `upload` step attaches
                # it. Registered under a fixed name so the step is stable across jobs, and under
                # the produced file's own name so an older step recorded against that still
                # resolves. Built here, where the job's values and line-item rows already are.
                grp = grp_for_mode
                if grp is not None and (grp.entry_mode or "fields") == "excel":
                    from app.core.excel_entry import build_for_job, describe

                    cfg = grp.excel_config or {}
                    tpl_dir = Path(get_settings().uploads_dir) / "excel_templates" / grp.id
                    tpl = (next((p for p in tpl_dir.glob("template.*")), None)
                           if cfg.get("source") == "template" else None)
                    try:
                        book = build_for_job(cfg, values, rows,
                                             Path(dl_dir) if dl_dir else Path("."), tpl,
                                             name_as=job.reference or job.id)
                        up_map.setdefault("excel_import", str(book))
                        up_map.setdefault(book.name, str(book))
                        # ...and under the template's own name, so a step recorded before the
                        # per-job naming still resolves. See the note on the other path.
                        if cfg.get("file_name"):
                            up_map.setdefault(str(cfg["file_name"]), str(book))
                        filled, mapped = _column_fill(cfg, values, rows)
                        empty = mapped - filled
                        logger.info("job %s: built %s for Excel entry (%s)%s",
                                    job.id, book.name, describe(cfg),
                                    f"; {empty} mapped column(s) had no value" if empty else "")
                    except Exception as exc:  # noqa: BLE001
                        # Loud, but as a FAILED JOB the operator can read - not a bare 500 with
                        # no reason and nothing persisted. Attaching nothing and letting the ERP
                        # accept an empty import is the one outcome that must never happen.
                        logger.exception("job %s: could not build the Excel import", job.id)
                        job.status = "failed"
                        job.erp_status = "error"
                        job.erp_reason = f"The import spreadsheet could not be built: {exc}"
                        db.commit()
                        raise HTTPException(
                            status_code=422,
                            detail=f"The import spreadsheet could not be built: {exc}",
                        ) from exc
                    # AN EMPTY WORKBOOK MUST NOT REACH THE ERP. The count above was only ever
                    # logged, so a job whose values had gone missing still built a perfectly
                    # valid file with nothing in it, uploaded it, and reported ok - which is
                    # exactly the outcome the note below calls unacceptable, with nothing
                    # actually stopping it. A build CRASH was caught; a build that quietly
                    # produced nothing was not.
                    #
                    # OUTSIDE the try, for the same reason the upload-step check is: this is a
                    # decided outcome, not a build error to be re-labelled by that handler.
                    if mapped and not filled:
                        detail = (
                            f"Every one of the {mapped} mapped columns is empty, so the import "
                            "spreadsheet would go to the ERP with no data in it. Nothing has "
                            "been entered. Check the job's extracted values before running the "
                            "entry again.")
                        logger.error("job %s: %s", job.id, detail)
                        job.status = "failed"
                        job.erp_status = "error"
                        job.erp_reason = detail
                        db.commit()
                        raise HTTPException(status_code=422, detail=detail)
                    # The workbook only reaches the ERP through a recorded `upload` step.
                    # Without one the run would submit an import with no file and still report
                    # ok - so refuse before touching the ERP, and say what to do about it.
                    # Deliberately OUTSIDE the try above: this is a decided outcome, not a
                    # build error to be re-labelled.
                    from app.core.browser import _resolve_upload

                    # Every name the workbook answers to. The file is named after the JOB now,
                    # so a step recorded against the TEMPLATE's file name must still match it -
                    # otherwise renaming the file silently breaks every existing recording and
                    # the job is refused for having "no upload step that attaches it".
                    book_only = {"excel_import": str(book), book.name: str(book)}
                    if cfg.get("file_name"):
                        book_only.setdefault(str(cfg["file_name"]), str(book))
                    if not any(
                        st.get("action") == "upload"
                        and _resolve_upload(str(st.get("value") or ""), book_only)
                        for st in (script.steps or [])
                    ):
                        others = sorted({
                            str(st.get("value") or "").strip()
                            for st in (script.steps or [])
                            if st.get("action") == "upload" and str(st.get("value") or "").strip()
                        })
                        detail = (
                            f"This customer is set to import a spreadsheet, and {book.name} was "
                            f"built for this job, but the ERP script {script.name!r} has no "
                            "upload step that attaches it"
                            + (f" (its upload steps attach {', '.join(others)})" if others
                               else " (it has no upload step at all)")
                            + ". Record the ERP's import screen: press its Browse button and "
                            "pick any spreadsheet, and every job will attach its own instead."
                        )
                        logger.error("job %s: %s", job.id, detail)
                        job.status = "failed"
                        job.erp_status = "error"
                        job.erp_reason = detail
                        db.commit()
                        raise HTTPException(status_code=422, detail=detail)
                # Publish each step's screen so the run can be WATCHED while it happens.
                # play_steps has offered this hook all along; a job run simply never passed
                # one, which is why a running entry was invisible until it finished.
                def _live(shot, log, debug=None, _jid=job.id):
                    live_runs.publish(_jid, shot, log, debug=debug)

                result = play_steps(
                    script.url, login, script.steps or [], values, headless=True, rows=rows,
                    downloads_dir=dl_dir, checkpoint_index=ckpt, session_file=sess_file,
                    uploads=up_map, progress=_live,
                )
                live_runs.publish(job.id, None, result.get("log") or [], done=True)
        except HTTPException:
            # Raised deliberately above (the workbook could not be built, or nothing would
            # attach it). The job's status and reason are already set and committed - resetting
            # them to "extracted" here would hide a decided outcome behind a retryable one.
            raise
        except Exception:
            job.status = "extracted"  # unexpected crash — let the operator retry
            db.commit()
            raise
        erp = persist_run_outcome(
            db, job, script, result,
            operator_id=user.id if user.role == OPERATOR else job.assigned_operator_id,
        )

    db.refresh(job)
    detail = _build_detail(db, job)
    for k, v in erp.items():
        setattr(detail, k, v)
    return detail


def _slot_for_new_file(db: Session, job: Job, template_document_id: str) -> JobDocument | None:
    """Where the next file for this slot should go.

    The empty row created with the job is filled first; after that each upload ADDS a row.
    A slot holds as many files as the shipment has — replacing the previous one is how three
    uploaded invoices used to become one, with nothing on screen to say so.

    Returns None if the job has no such slot at all.
    """
    existing = (
        db.query(JobDocument)
        .filter(JobDocument.job_id == job.id,
                JobDocument.template_document_id == template_document_id)
        .order_by(JobDocument.file_index)
        .all()
    )
    if not existing:
        return None
    free = next((d for d in existing if not d.file_path), None)
    if free is not None:
        return free
    jd = JobDocument(
        tenant_id=job.tenant_id,
        job_id=job.id,
        template_document_id=template_document_id,
        file_index=max(d.file_index for d in existing) + 1,
    )
    db.add(jd)
    db.flush()  # need jd.id to name its folder
    return jd


@router.post("/jobs/{job_id}/documents/{template_document_id}/upload", response_model=JobDetailOut)
def upload_job_document(
    job_id: str,
    template_document_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    job = _load_job(db, job_id, scope, user)
    if job.status == "extracting":
        # run_extraction snapshots this job's documents ONCE, at the top, and never
        # refreshes that list for the rest of its (multi-second to multi-minute) run - a
        # file landing here mid-run is invisible to the loop that actually writes its
        # fields, even though later steps that re-query fresh (custom-field computation)
        # do see it. Found live: a job ended up with one document's marks entirely blank
        # while everything else on it looked normal, because its file was uploaded while
        # extraction on the rest of the job was still in flight.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job's documents are being read right now — wait for extraction to finish before adding more.",
        )
    existing = (
        db.query(JobDocument)
        .filter(JobDocument.job_id == job.id, JobDocument.template_document_id == template_document_id)
        .order_by(JobDocument.file_index)
        .all()
    )
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job document slot not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"File type not allowed — use one of {sorted(ALLOWED_EXTENSIONS)}")
    if file.size is not None and file.size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit.")
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    # The same file uploaded twice into one slot is a slip, not a second document. Caught
    # here, because further down it is indistinguishable from a genuine extra invoice: it
    # would pair into the same set by its own number and sit there as a duplicate nobody
    # ordered. Compared by content, so a renamed copy is still caught.
    digest = hashlib.md5(content).hexdigest()
    dupe = None
    for d in existing:
        if not d.file_path:
            continue
        prev = Path(d.file_path)
        try:
            if (prev.exists() and prev.stat().st_size == len(content)
                    and hashlib.md5(prev.read_bytes()).hexdigest() == digest):
                dupe = d
                break
        except OSError:
            continue
    if dupe is not None:
        raise HTTPException(status_code=409, detail=(
            f"That exact file is already on this job as "
            f"{dupe.original_name or f'file {dupe.file_index + 1}'} — "
            "upload the next document instead."))

    jd = _slot_for_new_file(db, job, template_document_id)
    jd.original_name = (file.filename or "")[:255] or None

    ddir = _job_doc_dir(jd.id)
    try:
        ddir.mkdir(parents=True, exist_ok=True)
        original = ddir / f"original{ext}"
        original.write_bytes(content)
        page_count = _render_pages(original, ddir / "pages")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to render job document %s", jd.id)
        shutil.rmtree(ddir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Could not read the uploaded document. Is it a valid PDF or image?")

    jd.file_path = str(original)
    jd.page_count = page_count
    db.commit()
    _maybe_auto_extract(db, job)
    db.refresh(job)
    return _build_detail(db, job)


# ---- IRN Documents Upload (GK1) / IRN Processing (GK2): supporting documents + prealert -----
# Arbitrary files stored against a job under a label the operator types, and the original
# email a job was created from - see app/models/supporting_document.py and
# app/core/job_email.py for what these are and why they exist. Neither is OCR'd, classified,
# or extracted: this section is pure storage and retrieval.

MAX_SUPPORTING_FILE_BYTES = 25 * 1024 * 1024


def _supporting_doc_dir(doc_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "supporting_documents" / doc_id


def _supporting_document_out(row) -> dict:
    return {
        "id": row.id, "label": row.label, "files": row.files or [],
        "uploaded_by": row.uploaded_by, "created_at": row.created_at.isoformat(),
    }


@router.get("/jobs/{job_id}/supporting-documents")
def list_supporting_documents(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> dict:
    from app.models.supporting_document import SupportingDocument

    job = _load_job(db, job_id, scope, user)
    rows = (
        db.query(SupportingDocument)
        .filter(SupportingDocument.job_id == job.id)
        .order_by(SupportingDocument.created_at)
        .all()
    )
    return {"documents": [_supporting_document_out(r) for r in rows]}


@router.post("/jobs/{job_id}/supporting-documents", status_code=status.HTTP_201_CREATED)
def upload_supporting_documents(
    job_id: str,
    label: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> dict:
    """One or many files under one label the uploader chose - IRN documents, or anything else
    that needs to sit on the job with nothing done to it. Does NOT tick off GK1's IRN
    Documents Upload stage by itself any more - that now always needs an explicit press of
    Approval for IRN or Skip (see /jobs/{job_id}/irn-documents/approve and .../skip), even
    after uploading something here, so the choice between those two is always on record."""
    from app.models.supporting_document import SupportingDocument

    job = _load_job(db, job_id, scope, user)
    label = (label or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="Give this document a name.")
    if not files:
        raise HTTPException(status_code=422, detail="Choose at least one file.")

    row = SupportingDocument(tenant_id=job.tenant_id, job_id=job.id, label=label,
                             files=[], uploaded_by=user.id)
    db.add(row)
    db.flush()
    ddir = _supporting_doc_dir(row.id)
    ddir.mkdir(parents=True, exist_ok=True)
    stored = []
    for idx, f in enumerate(files):
        content = f.file.read(MAX_SUPPORTING_FILE_BYTES + 1)
        if len(content) > MAX_SUPPORTING_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename}: exceeds the 25 MB limit.")
        if not content:
            continue
        ext = Path(f.filename or "").suffix
        stored_as = f"{idx}{ext}"
        (ddir / stored_as).write_bytes(content)
        stored.append({
            "stored_as": stored_as, "original_name": (f.filename or "")[:255],
            "size": len(content),
        })
    if not stored:
        db.rollback()
        shutil.rmtree(ddir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Every file selected was empty.")
    row.files = stored
    db.commit()
    db.refresh(row)
    return _supporting_document_out(row)


@router.get("/jobs/{job_id}/supporting-documents/{doc_id}/files/{stored_as}")
def download_supporting_document_file(
    job_id: str,
    doc_id: str,
    stored_as: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    from app.models.supporting_document import SupportingDocument

    job = _load_job(db, job_id, scope, user)
    row = db.query(SupportingDocument).filter(
        SupportingDocument.id == doc_id, SupportingDocument.job_id == job.id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    match = next((f for f in (row.files or []) if f.get("stored_as") == stored_as), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = _supporting_doc_dir(row.id) / stored_as
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing on disk")
    return FileResponse(path, filename=match.get("original_name") or stored_as)


@router.delete("/jobs/{job_id}/supporting-documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supporting_document(
    job_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> None:
    from app.models.supporting_document import SupportingDocument

    job = _load_job(db, job_id, scope, user)
    row = db.query(SupportingDocument).filter(
        SupportingDocument.id == doc_id, SupportingDocument.job_id == job.id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    shutil.rmtree(_supporting_doc_dir(row.id), ignore_errors=True)
    db.delete(row)
    # irn_documents_done is now only ever set by the explicit Approval-for-IRN/Skip endpoints
    # (uploading no longer sets it - see upload_supporting_documents), so deleting a document
    # here must never touch it: GK1's already-recorded choice does not depend on which files
    # happen to still be attached afterward.
    db.commit()


@router.post("/jobs/{job_id}/irn-documents/approve")
def approve_irn_documents(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> dict:
    """One of GK1's two ways to tick off the IRN Documents Upload stage - said explicitly,
    the same as Skip, but remembered as the OTHER choice (see Job.irn_approval_requested):
    gk2_approve reads this to park the job in "IRN Document Process" instead of running the
    real ERP submission once GK2 gives their final approval."""
    job = _load_job(db, job_id, scope, user)
    job.irn_documents_done = True
    job.irn_approval_requested = True
    db.commit()
    return {"irn_documents_done": True, "irn_approval_requested": True}


@router.post("/jobs/{job_id}/irn-documents/skip")
def skip_irn_documents(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> dict:
    """The other way to tick off GK1's IRN Documents Upload stage: nothing needed attaching,
    said explicitly rather than just leaving the tab and hoping that counted."""
    job = _load_job(db, job_id, scope, user)
    job.irn_documents_done = True
    db.commit()
    return {"irn_documents_done": True}


@router.get("/jobs/{job_id}/prealert")
def get_prealert(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> dict:
    """The original email this job was created from, if it was created from one at all - see
    app/core/job_email.py. A job raised any other way (Excel entry, a manual upload) simply
    has none, which is not an error."""
    from app.core.job_email import has_original_eml, read_email_meta

    job = _load_job(db, job_id, scope, user)
    meta = read_email_meta(job.id)
    if meta is None:
        return {"available": False}
    return {
        "available": True,
        "sender": meta.get("sender"), "subject": meta.get("subject"),
        "received_at": meta.get("received_at"),
        "has_original_eml": has_original_eml(job.id),
        "attachments": meta.get("attachments") or [],
    }


@router.get("/jobs/{job_id}/prealert/original")
def download_prealert_original(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    from app.core.job_email import job_email_dir

    job = _load_job(db, job_id, scope, user)
    path = job_email_dir(job.id) / "original.eml"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No original email is on file for this job.")
    return FileResponse(path, filename=f"{job.reference}_prealert.eml")


@router.get("/jobs/{job_id}/prealert/attachments/{stored_as}")
def download_prealert_attachment(
    job_id: str,
    stored_as: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
):
    from app.core.job_email import job_email_dir, read_email_meta

    job = _load_job(db, job_id, scope, user)
    meta = read_email_meta(job.id) or {}
    match = next((a for a in (meta.get("attachments") or []) if a.get("stored_as") == stored_as), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = job_email_dir(job.id) / "attachments" / stored_as
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing on disk")
    return FileResponse(path, filename=match.get("name") or stored_as)


def _remove_document_file(db: Session, job: Job, jd: JobDocument) -> Path:
    """Clear one uploaded file from a job document slot - the wrong invoice, uploaded by
    mistake, or (see custom_filter_pages.py's old-job sweep) a page content-matching an
    admin's page filter. Removes any field values that came from it. Does not commit, and
    deliberately does NOT delete the file from disk - that is irreversible the instant it
    happens, while the caller's own db.commit() can still fail or be rolled back afterward
    (the sweep's own per-job try/except does exactly this on a later job's error). Returns
    the directory the caller must delete ONLY after its commit has actually succeeded, or a
    failure partway through a multi-document job (the sweep's normal shape) can leave a file
    permanently gone from disk while the rolled-back database still believes it is there."""
    siblings = (
        db.query(JobDocument)
        .filter(JobDocument.job_id == job.id,
                JobDocument.template_document_id == jd.template_document_id)
        .order_by(JobDocument.file_index)
        .all()
    )
    # Anything read from this file goes with it, or the job keeps reporting values from a
    # document that is no longer attached to it.
    db.query(JobFieldValue).filter(JobFieldValue.job_document_id == jd.id).delete()
    # Tenant CUSTOM fields (lookup/AI-computed/composite) have no job_document_id of their own
    # to key the delete above on - a field with no source_document_ids reads every document as
    # its fallback, so there is no reliable way to tell "did this field actually depend on the
    # file just removed" from the field's own config. A live job (JOB-7EED74) was found with
    # EVERY mark-based value gone this way while its custom fields sat there untouched,
    # computed from data that no longer existed, with nothing anywhere saying so.
    #
    # kind="hardcoded" is excluded from this wipe - it never reads any document at all, so a
    # document being removed has no bearing on it either way. Its value is only ever the
    # field's own static default or whatever an operator typed in by hand (e.g. a duty
    # notification number on Additional Details) - wiping it on an unrelated document swap
    # destroyed that operator's own input with no way for a later Extract to bring it back
    # (Extract only knows the static default, never what was actually typed).
    #
    # Only when this job has actually been extracted before (status != "draft") - losing an
    # upload before the first Extract is an operator swapping files, nothing stale exists yet.
    if job.status != "draft":
        from app.models.custom_field import CustomField

        non_hardcoded_cf_ids = [
            cid for (cid,) in db.query(CustomField.id)
            .filter(CustomField.group_id == job.group_id, CustomField.kind != "hardcoded").all()
        ]
        if non_hardcoded_cf_ids:
            db.query(JobFieldValue).filter(
                JobFieldValue.job_id == job.id,
                JobFieldValue.custom_field_id.in_(non_hardcoded_cf_ids),
            ).delete(synchronize_session=False)
        job.needs_reextraction = True
    doc_dir = _job_doc_dir(jd.id)

    if len(siblings) == 1:
        # The last file in the slot: keep the slot itself, empty, so it still asks to be
        # filled. _slot_for_new_file reuses exactly this now-empty row for whatever gets
        # uploaded next - so an approval left standing here would silently attach itself to
        # a completely different, never-reviewed file the moment the slot is refilled,
        # reading "already approved" to whichever reviewer opens it next. The content that
        # was actually approved is gone; the approval must go with it.
        jd.file_path = None
        jd.page_count = 0
        jd.original_name = None
        jd.extracted_json = None
        jd.set_index = None
        jd.approved = False
        jd.gk2_approved = False
    else:
        db.delete(jd)
        db.flush()
        for i, d in enumerate(x for x in siblings if x.id != jd.id):
            d.file_index = i
    return doc_dir


@router.delete("/jobs/{job_id}/documents/{job_document_id}/file", response_model=JobDetailOut)
def delete_job_document_file(
    job_id: str,
    job_document_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Remove ONE file from a slot — the wrong invoice, uploaded by mistake."""
    job = _load_job(db, job_id, scope, user)
    jd = (
        db.query(JobDocument)
        .filter(JobDocument.id == job_document_id, JobDocument.job_id == job.id)
        .first()
    )
    if jd is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found on this job")

    doc_dir = _remove_document_file(db, job, jd)
    db.commit()
    shutil.rmtree(doc_dir, ignore_errors=True)
    db.refresh(job)
    return _build_detail(db, job)


CLASSIFY_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}


@router.post("/jobs/{job_id}/smart-upload")
def smart_upload(
    job_id: str,
    files_in: list[UploadFile] = File(..., alias="files"),
    # Verification-only: returns each file's own OCR text and full per-claim evidence
    # alongside the ordinary result, instead of collapsing straight to matched slot names.
    # Never sent by the real Smart Upload UI - added to directly diagnose a real
    # misclassification (a bill of lading and an arrival notice swapping identities) without
    # guessing at what the classifier actually saw.
    debug: bool = False,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
):
    """Upload any mix of PDFs/images and ZIPs in one go; each file is OCR'd + classified
    (BL/PL/INV/Freight) and routed to the matching document slot(s). A combined file (e.g.
    BL+PL, or three invoices in one PDF) fills every slot/instance it matches. Returns the
    classification summary + updated job detail.

    Several files in one request matter, not just for convenience: the specificity rule below
    (which file wins a contested slot) only works when every file in the batch is classified
    together. Looping one file per request the way the browser used to is why three invoices
    sent one at a time each landed as if it were the ONLY invoice, instead of three rows in
    the same slot.
    """
    import fitz  # PyMuPDF

    job = _load_job(db, job_id, scope, user)
    if is_extraction_paused(db):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Extraction is currently paused by an administrator.")
    if job.status == "extracting":
        # See the identical guard in upload_job_document (same file, above) - same race,
        # same fix.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job's documents are being read right now — wait for extraction to finish before adding more.",
        )
    group = db.get(TemplateGroup, job.group_id)
    # `fields` lets a "Custom" document (no built-in content hint) still be classified,
    # by describing itself through the labels configured on it.
    candidates = [
        {
            "key": d.id,
            "name": d.name,
            "doc_type": d.doc_type,
            "fields": [m.label_name for m in d.marks],
        }
        for d in group.documents
    ]
    job_docs = {jd.template_document_id: jd for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all()}

    # Collect the files to process — each uploaded item as itself, or unzipped if it is a ZIP.
    files: list[tuple[str, bytes]] = []
    for uf in files_in:
        content = uf.file.read(50 * 1024 * 1024)
        fname = uf.filename or "upload"
        if fname.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    for n in z.namelist():
                        if not n.endswith("/") and Path(n).suffix.lower() in CLASSIFY_EXTS:
                            files.append((Path(n).name, z.read(n)))
            except zipfile.BadZipFile:
                raise HTTPException(status_code=422, detail=f"{fname}: invalid ZIP file.")
        else:
            files.append((fname, content))
    if not files:
        raise HTTPException(status_code=422, detail="No PDF/image files found to classify.")

    tmp = Path(get_settings().uploads_dir) / "_classify" / job_id
    shutil.rmtree(tmp, ignore_errors=True)
    custom_filter_texts = get_active_custom_filter_texts(db)
    results: list[dict] = []
    try:
        # PREPARE every file first, then assign them together. One slot can only take one file
        # and the whole set has to be weighed at once to decide which file that is - classifying
        # each file on its own and writing it straight in is what let one invoice fill both the
        # Invoice and the Packing List slot while the real packing list was dropped.
        prepared: list[dict] = []
        for idx, (name, data) in enumerate(files):
            ext = Path(name).suffix.lower() or ".pdf"
            if ext not in CLASSIFY_EXTS:
                continue
            fdir = tmp / str(idx)
            (fdir / "pages").mkdir(parents=True, exist_ok=True)
            orig = fdir / f"f{ext}"
            orig.write_bytes(data)

            text = ""
            img_path = None
            try:
                page_count = 0
                with fitz.open(orig) as doc:
                    for i, page in enumerate(doc, start=1):
                        if i > 6:  # cap pages OCR'd for classification
                            break
                        png = fdir / "pages" / f"page_{i}.png"
                        page.get_pixmap(dpi=150).save(png)
                        if i == 1:
                            img_path = png
                        page_count = i
                # OCR EVERY page (labelled) so a page-1-BL / page-2-PL combo is detected.
                raw_pages = []
                for i in range(1, page_count + 1):
                    try:
                        t = ocr_page_image(fdir / "pages" / f"page_{i}.png").get("text", "")
                    except Exception:  # noqa: BLE001 — OCR down; fall back to image classify
                        logger.warning("OCR unavailable for %s page %s; classifying from image", name, i)
                        t = ""
                    raw_pages.append(t)
                # Carrier terms out before CLASSIFYING, not only before extracting. Those 39,000
                # characters of legal prose define "Freight", "Packing List", "Package" and
                # "invoice", which is how one 2-page bill of lading came to match every slot on
                # the template and overwrite the real freight certificate.
                kept_pages, dropped_pages = filter_pages_with_custom(raw_pages, custom_filter_texts, name)
                if dropped_pages:
                    logger.info("smart upload: %s - ignoring page(s) %s, carrier terms",
                                name, dropped_pages)
                text = "\n\n".join(f"=== PAGE {n} ===\n{t}"
                                   for n, t in enumerate(kept_pages, start=1) if t.strip())
            except Exception:  # noqa: BLE001
                results.append({"filename": name, "matched": [], "error": "unreadable"})
                continue
            prepared.append({"name": name, "ext": ext, "blob": data,
                             "text": text, "image": img_path,
                             "drop_pages": dropped_pages, "page_count": page_count})

        claims_per_file = assign_documents_detailed(prepared, candidates) if prepared else []
        for item, claims in zip(prepared, claims_per_file):
            matched_names: list[str] = []
            # A combined file (one PDF carrying both the Invoice and the Packing List) must
            # give each slot only ITS pages, not the whole thing - see extract_pdf_pages.
            split = len(claims) > 1 and item["ext"] == ".pdf"
            kept_original = (
                kept_page_to_original(item["page_count"], item["drop_pages"]) if split else []
            )
            for claim in claims:
                key = claim["key"]
                # A new row per file, so a ZIP of three invoices lands as three invoices
                # instead of three writes over the same slot leaving only the last.
                jd = _slot_for_new_file(db, job, key)
                if jd is None:
                    continue
                jd.original_name = item["name"][:255] or None
                ddir = _job_doc_dir(jd.id)
                ddir.mkdir(parents=True, exist_ok=True)
                dorig = ddir / f"original{item['ext']}"
                if split:
                    orig_pages = sorted({
                        kept_original[p - 1] for p in claim["pages"]
                        if 1 <= p <= len(kept_original)
                    }) or kept_original
                    dorig.write_bytes(extract_pdf_pages(item["blob"], orig_pages))
                    jd.page_count = _render_pages(dorig, ddir / "pages")
                else:
                    dorig.write_bytes(item["blob"])
                    jd.page_count = _render_pages(dorig, ddir / "pages",
                                                  skip=item.get("drop_pages"))
                jd.file_path = str(dorig)
                matched_names.append(next(c["name"] for c in candidates if c["key"] == key))
            result_row = {"filename": item["name"], "matched": matched_names or None}
            if debug:
                result_row["debug_text"] = item["text"][:6000]
                result_row["debug_claims"] = claims
            results.append(result_row)
        db.commit()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _maybe_auto_extract(db, job)
    db.refresh(job)
    return {"results": results, "detail": _build_detail(db, job).model_dump()}


def _resolve_target_value(
    db: Session, job_id: str, cf, value: str | None,
) -> tuple[str | None, str | None]:
    """Apply an is_target_value field's reference-table lookup to a freshly computed value.
    Returns (value, raw_value) - raw_value is what a later operator correction gets learned
    against (see correct_field_value's own remember_reference call).

    Normally keyed on the field's OWN value - the raw extraction is never shown as-is, only
    looked up in this field's own reference table, exactly like a mark ticked this way.
    lookup_key_label (reused from the per-row "lookup" case, same meaning: which OTHER field
    to key off) instead names a DIFFERENT field on this job to key off. "Erp Entry Name" has
    nothing of its own to extract (kind="hardcoded", no fixed value) and is keyed entirely
    off "Quotation Name" instead, so the same quotation's forwarder is only ever typed in
    once, however many later jobs quote the same name. Shared by run_extraction's own loop
    and recompute_custom_field, which must resolve a target-value field the same way a fresh
    extraction would - two copies of this is how the two quietly drift apart.
    """
    if not getattr(cf, "is_target_value", False):
        return value, value
    from app.core.reference_cache import lookup_reference

    key_field_label = (getattr(cf, "lookup_key_label", None) or "").strip()
    if key_field_label:
        key_fv = (
            db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job_id, JobFieldValue.label_name == key_field_label,
                    JobFieldValue.row_index.is_(None))
            .first()
        )
        key_value = (
            (key_fv.corrected_value or key_fv.extracted_value or "").strip() if key_fv else ""
        )
        if not key_value:
            return None, None
        resolved = lookup_reference(db, custom_field_id=cf.id, match_values=[key_value],
                                    fuzzy=getattr(cf, "fuzzy_match", False))
        return (resolved or None), key_value
    if not value:
        return value, value
    resolved = lookup_reference(db, custom_field_id=cf.id, match_values=[value],
                                fuzzy=getattr(cf, "fuzzy_match", False))
    return (resolved or None), value


def _composite_piece_value(piece, piece_values: dict[str, dict], key) -> str:
    """One piece of a composite field's value, for one line.

    A piece is either a plain string (another field's own label_name - read from
    piece_values, keyed exactly the way _line_values/_row_values already return per-line
    values) or {"fixed": "<literal text>"} - a fixed/literal piece the Super Admin or an
    operator typed in directly (e.g. a separator, a fixed prefix), the SAME text on every
    line rather than something read off the job. Shared by run_extraction's per-row loop and
    _recompute_per_row_custom_field so the two never drift on what a "piece" can be.
    """
    if isinstance(piece, str):
        return piece_values.get(piece, {}).get(key, "")
    if isinstance(piece, dict):
        return str(piece.get("fixed") or "")
    return ""


# The one job-level field this job's own consignee is read off of, for
# CompositeFieldConsigneeDefault's lookup key - see that model's own docstring for why this is
# an exact-text match rather than the fuzzy_match CustomField already has elsewhere.
_CONSIGNEE_LABEL = "consignee_full_name"


def _job_consignee_key(db: Session, job: Job) -> str:
    # This session runs with autoflush off (see SessionLocal) - a same-run write to
    # consignee_full_name (job-level, processed earlier in run_extraction's own custom_fields
    # loop) would otherwise still be sitting unflushed and invisible to this query.
    db.flush()
    fv = (
        db.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.is_(None),
                JobFieldValue.label_name == _CONSIGNEE_LABEL)
        .first()
    )
    if fv is None:
        return ""
    return (fv.corrected_value or fv.extracted_value or "").strip()


def _effective_composite_pieces(db: Session, job: Job, cf: "CustomField") -> list:
    """Which pieces a composite field ACTUALLY computes with for this one job.

    A consignee that has already had this field applied once (anywhere - any job, any
    operator) reuses that same field ORDER on every later job of theirs automatically, rather
    than each new job starting from the template's generic fallback - see
    CompositeFieldConsigneeDefault. No consignee match (a first-time consignee, or a job with
    no consignee value at all) falls back to the field's own composite_source_labels exactly
    as before this existed.
    """
    from app.models.composite_consignee_default import CompositeFieldConsigneeDefault

    key = _job_consignee_key(db, job)
    if key:
        override = (
            db.query(CompositeFieldConsigneeDefault)
            .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                    CompositeFieldConsigneeDefault.consignee_key == key)
            .first()
        )
        if override is not None:
            return list(override.source_labels or [])
    return getattr(cf, "composite_source_labels", None) or []


def run_extraction(db: Session, job: Job) -> None:
    """Extract every marked field for a job's uploaded documents and set status to
    'extracted'. Reusable by the operator's Extract button AND the email auto-pull."""
    if is_extraction_paused(db):
        # A Super Admin turned extraction off (see app/api/v1/system_settings.py) - before
        # any OCR or AI call, not after one fails. The two existing callers already handle
        # this cleanly: the background thread reverts a job stuck "extracting" back to
        # "draft" so it is not shown as processing forever, and the email puller's own
        # try/except just logs and leaves the job routed but not yet field-extracted -
        # either way, pressing Extract again once resumed is all that is needed.
        raise AIServiceUnavailable("extraction is paused")
    group = db.get(TemplateGroup, job.group_id)
    # A slot can hold several files — three invoices are three rows sharing one
    # template_document_id — so this is a LIST per slot, in upload order, not one document.
    # Reading only the first is what silently dropped invoices 2 and 3.
    docs_by_tdoc: dict[str, list[JobDocument]] = {}
    for _jd in (db.query(JobDocument)
                .filter(JobDocument.job_id == job.id)
                .order_by(JobDocument.file_index).all()):
        docs_by_tdoc.setdefault(_jd.template_document_id, []).append(_jd)

    # A "Re-run extraction" re-reads the documents, it does not mean "and throw away
    # whatever a human already typed" - the ERP rerun button's own tooltip already promises
    # exactly that ("Everything you have filled in is kept"), extraction's own re-run just
    # never lived up to it. Snapshotted before the delete below wipes it; restored into the
    # freshly-written rows further down (see the keyouted-snapshot loop). A field that
    # didn't exist before this run (a newly-added document, say) has no matching key and is
    # correctly left as freshly extracted - nobody has corrected it yet.
    #
    # Keyed on (mark_id, custom_field_id, job_document_id, row_index, set_index) - but
    # set_index is folded to a constant for any row that also carries a job_document_id
    # (every MARK-based row): that id alone already pins down the exact file, and its OWN
    # set_index is not always stable across a re-run (adding a second file to an
    # invoice-less slot like Bill of Lading can flip every file in that slot from the
    # "one-file-per-slot, force set 1" shortcut to genuinely unpaired/None, even though
    # neither existing file itself changed) - found by this fix's OWN test breaking. A
    # PER-ROW CUSTOM field has no job_document_id at all (see the write loop further down),
    # so its set_index is the only thing telling "row 1 of set 1" apart from "row 1 of set
    # 2" - two genuinely different products - and that one IS what needs to stay part of
    # the key.
    def _correction_key(fv) -> tuple:
        set_component = None if fv.job_document_id else fv.set_index
        return (fv.mark_id, fv.custom_field_id, fv.job_document_id, fv.row_index, set_component)

    old_corrections = {
        _correction_key(fv): fv.corrected_value
        for fv in db.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.corrected_value.isnot(None))
        .all()
    }

    # Clear any prior run.
    db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).delete()
    # A fresh extraction makes the job current again by definition - see
    # Job.needs_reextraction / _remove_document_file for how it gets set.
    job.needs_reextraction = False
    db.flush()

    doc_text_cache: dict[str, str] = {}  # keyed by JobDocument.id — per FILE, not per slot
    # Same key, this FILE's own OCR word boxes per (kept) page — see locate_value_bbox. Never
    # populated on the vision-fallback path (no OCR ran at all there, nothing to locate with).
    doc_tokens_cache: dict[str, list[tuple[int, list[dict]]]] = {}
    # Same key: the row count Document AI's own table geometry detected for this file, summed
    # across its (kept) pages - 0 when Document AI found no ruled/structured table anywhere in
    # it (a borderless layout, or simply no line-item table on this document). See
    # docai.py's _table_row_count/ocr_page_image and the multi-value cross-check below, which
    # treats 0 as "no signal available" rather than "zero rows".
    doc_table_row_count_cache: dict[str, int] = {}
    tdoc_by_id = {d.id: d for d in group.documents}
    custom_filter_texts = get_active_custom_filter_texts(db)

    def text_for_doc(jdoc: JobDocument) -> str:
        """Cached OCR text for ONE uploaded file.

        Also persists the raw OCR onto job_documents.extracted_json, so it lives in the
        database rather than only in the on-disk page cache.
        """
        if jdoc.id in doc_text_cache:
            return doc_text_cache[jdoc.id]
        tdoc_id = jdoc.template_document_id
        parts: list[str] = []
        tokens_by_page: dict[int, list[dict]] = {}
        table_row_counts_by_page: dict[int, list[int]] = {}
        if jdoc and jdoc.file_path:
            for page in range(1, jdoc.page_count + 1):
                try:
                    page_ocr = get_page_ocr(_job_doc_dir(jdoc.id), page)
                    # layout_text reassembles the page from Document AI's OWN paragraph/table
                    # grouping instead of its flat word-by-word stream - a table's columns
                    # stay columns and a multi-column page's two halves stay separate, rather
                    # than interleaving into one scrambled line. Falls back to the flat text
                    # for a processor response with nothing to group (see docai.py).
                    # structured_text is layout_text's reading-order fix for genuine multi-column
                    # regions (app/core/structure_engine.py); only used when the flag is on and
                    # the engine actually found something to fix on this page, else identical.
                    parts.append(
                        (get_settings().structure_engine_enabled and page_ocr.get("structured_text"))
                        or page_ocr.get("layout_text")
                        or page_ocr.get("text", "")
                    )
                    if page_ocr.get("tokens"):
                        tokens_by_page[page] = page_ocr["tokens"]
                    if page_ocr.get("table_row_counts"):
                        table_row_counts_by_page[page] = page_ocr["table_row_counts"]
                except Exception:  # noqa: BLE001
                    # Never swallow this silently. A failure here empties the OCR text, which
                    # drops the whole job to reading page images — much less accurate — and
                    # previously the reason (bad credentials, quota, missing page render) was
                    # discarded here, so nothing downstream could report why accuracy fell.
                    logger.exception(
                        "OCR FAILED for job doc %s page %d — falling back to page images",
                        jdoc.id, page,
                    )
        tdoc = tdoc_by_id.get(tdoc_id)
        # Strip carrier terms-and-conditions pages. A sea waybill's terms page is 39,572 of its
        # 43,956 characters and holds no data — it crowded other documents out of the shared
        # budgets and its prose is where "Blue Anchor" and "FREIGHT COLLECT" were mistaken for
        # a vessel name and an incoterm. Position is not fixed, so each page is judged alone.
        kept, dropped_pages = filter_pages_with_custom(parts, custom_filter_texts, tdoc.name if tdoc else "document")
        doc_text_cache[jdoc.id] = "\n".join(kept)
        # Same drop list applies to the token boxes — a value's text should never be "found"
        # on a terms-and-conditions page extraction itself ignored.
        doc_tokens_cache[jdoc.id] = [
            (p, tokens_by_page[p]) for p in range(1, len(parts) + 1)
            if p not in dropped_pages and p in tokens_by_page
        ]
        # Same drop list once more — a stripped page's table (if it even had one) never counts
        # toward this document's detected row total. Summed across every table on every kept
        # page: most documents have exactly one line-item table on exactly one page, but a
        # table split across two pages must still add up to the printed whole.
        doc_table_row_count_cache[jdoc.id] = sum(
            sum(counts) for p, counts in table_row_counts_by_page.items() if p not in dropped_pages
        )
        if jdoc is not None and parts:
            jdoc.extracted_json = {
                "document": tdoc.name if tdoc else None,
                "doc_type": tdoc.doc_type if tdoc else None,
                "file": jdoc.original_name,
                "page_count": len(parts),
                # Every page as OCR'd, unfiltered — this is the raw record.
                "pages": parts,
                # Which of them extraction ignored, and the text it actually saw.
                "excluded_pages": dropped_pages,
                "text": doc_text_cache[jdoc.id],
            }
        return doc_text_cache[jdoc.id]

    def text_for(tdoc_id: str) -> str:
        """Every file in one slot, joined — for custom AI fields, which reason about the slot
        as a whole ("from the invoice") rather than about one particular file. Each file is
        headed with its own name so the model can tell three invoices apart instead of
        reading them as one run-on document."""
        out = []
        for d in docs_by_tdoc.get(tdoc_id, []):
            if not d.file_path:
                continue
            body = text_for_doc(d)
            if not body.strip():
                continue
            out.append(f"--- file: {d.original_name or d.id[:8]} ---\n{body}" if len(
                docs_by_tdoc.get(tdoc_id, [])) > 1 else body)
        return "\n\n".join(out)

    all_values: dict[str, str] = {}
    # label -> value for each uploaded file, kept apart so the invoice numbers can be read
    # back afterwards and the documents paired on them.
    per_file: dict[str, dict[str, str]] = {}
    # Every value written this run, so set_index can be stamped once pairing is known.
    written: list[JobFieldValue] = []

    # Every FILE, not every slot: a slot holding three invoices is read three times, and each
    # file's values are kept under its own job_document_id so three invoice numbers stay three
    # invoice numbers instead of overwriting one another.
    for tdoc, jd in [(t, d) for t in group.documents for d in docs_by_tdoc.get(t.id, [])]:
        if jd.file_path is None or not tdoc.marks:
            continue

        ocr_text = text_for_doc(jd)

        def _spec(m, inline_format: bool = True):
            # One implementation, in extraction.py, shared with the wizard's demo and its
            # test-extract. They were three near-identical copies and only this one passed the
            # anchor and the example, so the wizard measured a different configuration from the
            # one that runs.
            return field_spec(m, inline_format=inline_format)

        # Fields ticked "multiple values" describe a line-item table and are read together
        # in one pass so their rows stay aligned; the rest are read as single values.
        single_marks = [m for m in tdoc.marks if not m.is_multi_value]
        multi_marks = [m for m in tdoc.marks if m.is_multi_value]
        fields = [_spec(m) for m in single_marks]

        used_vision = not ocr_text.strip()
        # Computed unconditionally (cheap - just a filesystem check): the row cross-check below
        # needs these page images even when OCR text is present and single-value extraction
        # stays text-based.
        image_paths: list = [_job_doc_dir(jd.id) / "pages" / f"page_{p}.png" for p in range(1, jd.page_count + 1)]
        image_paths = [p for p in image_paths if p.exists()]
        if used_vision:
            # WARNING, not INFO: reading page images instead of OCR text measurably degrades
            # accuracy, and until now it happened silently — a job could be read entirely from
            # images and look completely normal.
            logger.warning(
                "OCR text empty for job doc %s (%s) — falling back to vision on %d page image(s)",
                jd.id, tdoc.name, len(image_paths),
            )

        if not used_vision:
            extracted = extract_document_fields(ocr_text, fields)
        else:
            extracted = extract_document_fields_from_images(image_paths, fields)

        for mark in single_marks:
            val = extracted.get(mark.label_name)
            # is_target_value: the raw extraction is never shown as-is. It is looked up in
            # this mark's own reference table (keyed on nothing but itself - see
            # CustomFieldReferenceValue.mark_id); a match REPLACES it with the table's stored
            # value, no match returns empty rather than the raw text. target_value_raw keeps
            # the original so a later operator correction can still be remembered against the
            # right key even though `val`/extracted_value itself is now blank.
            raw_val = val
            if mark.is_target_value and val:
                from app.core.reference_cache import lookup_reference
                resolved = lookup_reference(db, mark_id=mark.id, match_values=[val],
                                            fuzzy=getattr(mark, "fuzzy_match", False))
                val = resolved or None
            # all_values is keyed by label, but the same label is marked on several documents
            # for cross-checking. A blank must never overwrite a value we already found: the
            # Freight Certificate carries no invoice number, and processing it last was wiping
            # the Invoice's. First non-empty wins — documents are ordered most-authoritative-first.
            if val or mark.label_name not in all_values:
                all_values[mark.label_name] = val or ""
            if val:
                per_file.setdefault(jd.id, {}).setdefault(mark.label_name, val)
            # Where this value's own text actually sits on THIS document — never the
            # template's static mark position, which is only right when a real document
            # happens to share the template sample's exact layout. None (no vision fallback,
            # or no confident text match) means the frontend shows no highlight at all.
            found = locate_value_bbox(doc_tokens_cache.get(jd.id, []), val) if val else None
            # Idempotent on the same reasoning as the multi-value and per-row loops elsewhere
            # in this function - see the per-row custom field loop's own comment for why.
            fv = (
                db.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id,
                        JobFieldValue.job_document_id == jd.id, JobFieldValue.row_index.is_(None))
                .first()
            )
            if fv is not None:
                fv.label_name = mark.label_name
                fv.extracted_value = val
                fv.target_value_raw = raw_val if mark.is_target_value else None
                fv.found_page = found[0] if found else None
                fv.found_x = found[1] if found else None
                fv.found_y = found[2] if found else None
                fv.found_width = found[3] if found else None
                fv.found_height = found[4] if found else None
            else:
                fv = JobFieldValue(
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    mark_id=mark.id,
                    template_document_id=tdoc.id,
                    job_document_id=jd.id,
                    label_name=mark.label_name,
                    extracted_value=val,
                    target_value_raw=raw_val if mark.is_target_value else None,
                    found_page=found[0] if found else None,
                    found_x=found[1] if found else None,
                    found_y=found[2] if found else None,
                    found_width=found[3] if found else None,
                    found_height=found[4] if found else None,
                )
                db.add(fv)
            written.append(fv)

        # One JobFieldValue per table row, tagged with row_index so the columns line up:
        # row 3's description, quantity and price all carry row_index 3.
        def _row_read_looks_broken(candidate_rows: list, labels: list[str]) -> bool:
            """A field that comes back IDENTICAL across every single row, despite several rows
            existing, is usually a header/section label bleeding into every row (a table's own
            "VVVF" heading misread as every row's own PO Number) rather than genuine per-row
            data. Used to keep a read like that from being trusted just because it happens to
            have more rows than another.

            Skips identity/descriptive columns on purpose - a description, a material or part
            code, a unit price, a unit-of-measure - because those CAN legitimately be identical
            on every row of a real document: three PO numbers for the SAME product, at the SAME
            price, correctly print the same description, material code and price on all three
            rows (see JOB-290770 - three genuine rows, one product, three POs, correctly
            identical on exactly those columns). This check used to flag that CORRECT read as
            broken forever, because 3-of-3 identical is what RIGHT looks like for those fields,
            not a header bleeding through - and once broken, every downstream cross-check that
            depends on "not broken" (see sums_disagree and the printed-total check below) never
            ran again for this document. PO numbers, quantities and amounts are never excluded -
            those are per-transaction facts that essentially never coincide by chance on
            genuinely different rows, so identical readings across three or more of THEM really
            is the original warning sign this function exists to catch.
            """
            if len(candidate_rows) < 3:
                return False
            identity_pattern = re.compile(
                r"(description|material|part.?code|unit.?price|quantity.?type|uom|unit.?of.?measure)",
                re.IGNORECASE,
            )
            for label in labels:
                if identity_pattern.search(label):
                    continue
                values = [r.get(label) for r in candidate_rows if r.get(label)]
                if len(values) >= 3 and len(set(values)) == 1:
                    return True
            return False

        if multi_marks:
            row_specs = [_spec(m, inline_format=False) for m in multi_marks]
            row_labels = [f["label"] for f in row_specs]
            # Document AI's own detected table geometry (see docai.py's _table_row_count) - 0
            # when it found no ruled/structured table on this document at all, treated
            # everywhere below as "no signal available", never as "zero rows". Passed into
            # BOTH readers as a prompt hint (helps the FIRST pass, not just this cross-check)
            # and used below as a third, independent source of truth for "how many rows are
            # there really" - the two LLM reads otherwise only ever argue with each other.
            detected_row_count = doc_table_row_count_cache.get(jd.id, 0)
            if not used_vision:
                rows = extract_document_rows(ocr_text, row_specs, detected_row_count or None)
                # Line-item tables are the highest-error part of extraction: a table whose OCR
                # text doesn't preserve visual column order can make the model silently drop or
                # merge rows (see ROW_IS_A_PRODUCT in extraction.py) no matter how the prompt is
                # worded, because the text itself no longer carries the row boundaries. The
                # rendered page image doesn't suffer that scrambling, so cross-check it whenever
                # it's available. Prefer it when it found MORE rows (the demonstrated failure
                # mode is rows going missing or merging, never spurious extra ones) OR when the
                # text read itself looks broken - but never prefer a candidate that itself looks
                # broken just because it has a higher row count.
                if image_paths:
                    vision_rows = extract_document_rows_from_images(
                        image_paths, row_specs, detected_row_count or None)
                    vision_broken = _row_read_looks_broken(vision_rows, row_labels)
                    text_broken = _row_read_looks_broken(rows, row_labels)
                    # Row counts can agree while the VALUES don't - see _row_sums_disagree for
                    # the live failure this closes that the count/looks-broken checks above
                    # structurally cannot. Only checked when neither read already looks broken
                    # and the counts already agree - a real count difference is handled above,
                    # and a read that looks broken is already headed for replacement regardless.
                    sums_disagree = (
                        not vision_broken and not text_broken and len(vision_rows) == len(rows)
                        and _row_sums_disagree(rows, vision_rows, row_labels)
                    )
                    # Both reads agreeing on a count neither the printed total NOR Document
                    # AI's own detected table corroborates is a failure mode nothing here can
                    # actually fix (which row is missing/extra isn't knowable from a count
                    # alone) - but it's exactly the kind of silent wrong answer that should at
                    # least be visible in the logs instead of looking like agreement settled it.
                    if (not vision_broken and not text_broken and len(vision_rows) == len(rows)
                            and detected_row_count and len(rows) != detected_row_count):
                        logger.warning(
                            "multi-value on %s: text and image both read %d row(s), but "
                            "Document AI's own table layout detected %d - neither reader "
                            "matches; keeping the agreed-upon read since which row is "
                            "missing/extra isn't knowable from a count alone",
                            tdoc.name, len(rows), detected_row_count,
                        )
                    prefer_vision = bool(
                        vision_rows and not vision_broken
                        and (text_broken or len(vision_rows) > len(rows))
                    )
                    settled = False
                    # A row-COUNT disagreement defaults to "more rows wins" above, on the
                    # assumption a drop/merge losing a row is the only real failure mode - but
                    # that assumption is exactly what a wrongly-INCLUDED total/subtotal row
                    # breaks (a document with 20 real rows read as 22). Document AI's own
                    # detected table row count is a genuine third, independent signal - neither
                    # LLM read's own guess - so it settles a count disagreement ahead of
                    # everything else whenever it's available and only one candidate matches it.
                    if not vision_broken and not text_broken and len(vision_rows) != len(rows) and detected_row_count:
                        text_matches_detected = len(rows) == detected_row_count
                        vision_matches_detected = len(vision_rows) == detected_row_count
                        if text_matches_detected != vision_matches_detected:
                            prefer_vision = vision_matches_detected
                            settled = True
                            logger.warning(
                                "multi-value on %s: %d row(s) from text vs %d from the image - "
                                "settled by Document AI's own detected table row count (%d), "
                                "which only %s matches",
                                tdoc.name, len(rows), len(vision_rows), detected_row_count,
                                "the image" if vision_matches_detected else "the text",
                            )
                    # A row-COUNT disagreement defaults to "more rows wins" above, on the
                    # assumption a drop/merge losing a row is the only real failure mode - but
                    # JOB-290770 disproved that: the image read invented a 4th row by mistaking
                    # a PO number for a new product (item_quantity summed to 3304), while the
                    # text read's 3 rows summed to exactly 5226 - this invoice's own printed
                    # total_quantity. Where the template captures a column's own total as a
                    # plain field alongside its row-level one, that total names which candidate
                    # actually adds up, and settles it ahead of row count entirely. Skipped once
                    # the detected-row-count check above already settled it.
                    if not settled and not vision_broken and not text_broken and len(vision_rows) != len(rows):
                        for label in row_labels:
                            known_total = _numeric_total_field(extracted, label)
                            if known_total is None:
                                continue
                            text_sum = numeric_total([r.get(label) for r in rows])
                            vision_sum = numeric_total([r.get(label) for r in vision_rows])
                            if text_sum is None or vision_sum is None:
                                continue
                            text_matches = math.isclose(text_sum, known_total, rel_tol=0.02, abs_tol=0.5)
                            vision_matches = math.isclose(vision_sum, known_total, rel_tol=0.02, abs_tol=0.5)
                            if text_matches != vision_matches:
                                prefer_vision = vision_matches
                                logger.warning(
                                    "multi-value on %s: %d row(s) from text vs %d from the image - "
                                    "settled by %r, whose printed total %s only %s's row sum matches",
                                    tdoc.name, len(rows), len(vision_rows), label, known_total,
                                    "the image" if vision_matches else "the text",
                                )
                                break
                    if prefer_vision:
                        logger.warning(
                            "multi-value on %s: OCR text read %d row(s)%s but the page image "
                            "read %d - using the image result",
                            tdoc.name, len(rows), " (looked broken)" if text_broken else "",
                            len(vision_rows),
                        )
                        rows = vision_rows
                    elif sums_disagree:
                        # SAME row count, at least one column's values genuinely disagree - but
                        # a wholesale swap trades one column's accuracy for another's rather
                        # than fixing the one that was actually wrong (JOB-290770 again: vision
                        # finally got item_quantity right, matching total_quantity, but its
                        # reading of the tiny material code and PO number was WORSE than text's
                        # - swapping the whole row lost ground it didn't need to). Merge PER
                        # COLUMN instead: a column with a matching printed total is settled by
                        # it; a column with no total to check but a genuine numeric
                        # disagreement keeps the existing default of trusting the image (the
                        # rendered page doesn't suffer OCR's column-order scrambling - see the
                        # comment above); a column neither numeric nor disagreeing (a
                        # description, a material code) is left as text read it, on the
                        # grounds that plain disagreement alone is no evidence THAT column was
                        # the one that broke.
                        for label in row_labels:
                            text_col = [r.get(label) for r in rows]
                            vision_col = [r.get(label) for r in vision_rows]
                            text_sum = numeric_total(text_col)
                            vision_sum = numeric_total(vision_col)
                            if text_sum is None or vision_sum is None:
                                continue
                            known_total = _numeric_total_field(extracted, label)
                            if known_total is not None:
                                text_matches = math.isclose(text_sum, known_total, rel_tol=0.02, abs_tol=0.5)
                                vision_matches = math.isclose(vision_sum, known_total, rel_tol=0.02, abs_tol=0.5)
                                use_vision = vision_matches and not text_matches
                                reason = f"its printed total {known_total!r}"
                            elif not math.isclose(text_sum, vision_sum, rel_tol=0.02, abs_tol=0.5):
                                use_vision = True
                                reason = "text vs image disagreeing on this column alone"
                            else:
                                continue
                            if use_vision:
                                logger.warning(
                                    "multi-value on %s: column %r settled by %s - using the "
                                    "image's reading for that column only",
                                    tdoc.name, label, reason,
                                )
                                for i, row in enumerate(rows):
                                    row[label] = vision_col[i]
            else:
                # Previously this branch required OCR text, so with none the line items were
                # dropped outright while the single fields still returned — an invoice would
                # come back with a supplier and a total but no products at all.
                rows = extract_document_rows_from_images(image_paths, row_specs, detected_row_count or None)
            logger.warning(
                "multi-value on %s: %d row(s) from %s",
                tdoc.name, len(rows), "page images" if used_vision else "OCR text",
            )
            for i, row in enumerate(rows, start=1):
                for mark in multi_marks:
                    value = row.get(mark.label_name)
                    # Idempotent on the same reasoning as the per-row custom field loop below -
                    # see its own comment. set_index isn't stamped until pairing runs, further
                    # down, so job_document_id (already fixed at this point) is the key instead.
                    fv = (
                        db.query(JobFieldValue)
                        .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id,
                                JobFieldValue.job_document_id == jd.id, JobFieldValue.row_index == i)
                        .first()
                    )
                    if fv is not None:
                        fv.extracted_value = value
                        fv.label_name = mark.label_name
                    else:
                        fv = JobFieldValue(
                            tenant_id=job.tenant_id,
                            job_id=job.id,
                            mark_id=mark.id,
                            template_document_id=tdoc.id,
                            job_document_id=jd.id,
                            label_name=mark.label_name,
                            # row_index stays the line number WITHIN this document, so an
                            # invoice's line 3 still lines up with its packing list's line 3.
                            # What separates invoice 2's line 3 from invoice 1's is set_index,
                            # stamped below once the documents have been paired.
                            row_index=i,
                            extracted_value=value,
                        )
                        db.add(fv)
                    written.append(fv)
            # Custom AI fields expect a single value, so give them the first row.
            for mark in multi_marks:
                first = (rows[0].get(mark.label_name) if rows else None) or ""
                if first or mark.label_name not in all_values:
                    all_values[mark.label_name] = first

    # ---- pair each invoice with its own packing list ---------------------------------------
    # Only possible now: the pairing is made on the invoice numbers, and those had to be read
    # off the documents first. Never on upload order — see app/core/doc_sets.py.
    from app.core.doc_sets import assign_sets, describe_sets, pairing_key

    paired_files = [
        {
            "id": d.id,
            "template_document_id": d.template_document_id,
            "file_index": d.file_index,
            "name": d.original_name or f"{tdoc.name} #{d.file_index + 1}",
            "key": pairing_key(per_file.get(d.id, {})),
        }
        for tdoc in group.documents
        for d in docs_by_tdoc.get(tdoc.id, [])
        if d.file_path
    ]
    doc_sets = assign_sets(paired_files)
    for _lst in docs_by_tdoc.values():
        for d in _lst:
            d.set_index = doc_sets.get(d.id)
    for fv in written:
        fv.set_index = doc_sets.get(fv.job_document_id)
    for _line in describe_sets(paired_files, doc_sets):
        logger.info("job %s — %s", job.reference, _line)
    unpaired = [f["name"] for f in paired_files
                if doc_sets.get(f["id"]) is None and f.get("key")]
    if unpaired:
        # Carries an invoice number that matches nothing else on the job. Said out loud
        # rather than quietly attached to set 1.
        logger.warning("job %s — not paired with any other document: %s",
                       job.reference, ", ".join(unpaired))
    db.flush()

    # ---- one compact JSON record per file --------------------------------------------------
    # The map step's output, and what lets a field reason across twenty documents. Each file
    # was read in full on its own; a record keeps what was READ and drops the paper around it
    # — the letterhead, the addresses, the terms — so twenty records cost roughly a fifth of
    # twenty raw documents and nothing that matters is cut. Raw text stays available as
    # supporting evidence, but it is no longer what the aggregate is computed from.
    doc_records = _document_records(db, job, group)
    logger.info("job %s — %d document record(s) for the computed fields, %d chars of JSON",
                job.reference, len(doc_records),
                len(json.dumps(doc_records, ensure_ascii=False)))

    # Custom fields: hardcoded values, or AI-computed from the selected documents.
    from app.models.custom_field import CustomField

    custom_fields = db.query(CustomField).filter(CustomField.group_id == group.id).all()
    # A field with lookup_key_label reads ANOTHER field's value on this SAME job (see
    # _resolve_target_value) - it must be computed AFTER whatever it depends on, or it reads
    # that field's value from BEFORE this run instead of the one this run is about to write.
    # "Erp Entry Name" (keyed on "Quotation Name") came back empty on a job whose Quotation
    # Name had never been computed before, purely because a plain query has no ordering
    # guarantee and this run happened to process Erp Entry Name first. Stable sort: fields
    # with no lookup_key_label keep their original relative order and go first; fields that
    # key off another field go last, after whatever they depend on has already run. A
    # composite field can name ANY other field (mark or custom field) as one of its pieces, so
    # it is held back the same way - last of all, after every other kind has already written
    # its own per-row value for this run.
    custom_fields = sorted(
        custom_fields,
        key=lambda cf: (
            getattr(cf, "kind", None) == "composite",
            bool(getattr(cf, "lookup_key_label", None)),
        ),
    )

    # How many line items this job actually has, taken from the rows extraction just wrote.
    # A per-row custom field gets exactly this many slots, so the CTH the operator types for
    # line 3 lines up with line 3's description, quantity and price.
    db.flush()
    # A product line is identified by the invoice-set it was billed on AND its line number on
    # that document — (set 2, line 3), not "line 3". Counting row_index alone returned 5 for
    # three five-line invoices, so ten of the fifteen products would have had no slot created
    # for their CTH at all, and nothing would have said so.
    line_keys: list[tuple[int, int]] = sorted({
        ((r[0] or 1), r[1])
        for r in db.query(JobFieldValue.set_index, JobFieldValue.row_index)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.isnot(None))
        .distinct().all()
    })
    line_count = len(line_keys)
    # The customer's material master, if they have uploaded one. Loaded once for the whole job
    # rather than per line: it is the same file for every line and every field that looks
    # something up in it.
    from app.core.material_master import load_master, lookup_cth, material_from
    from app.core.reference_cache import lookup_reference

    def _line_values(label: str) -> dict[tuple[int, int], str]:
        """One line-item field's value per line, e.g. the material code per invoice line.

        Keyed by (set, line) — with three invoices on the job, "line 3" is three different
        products and keying on the line number alone let the last one read overwrite the
        other two.
        """
        out: dict[tuple[int, int], str] = {}
        for fv in db.query(JobFieldValue).filter(
                JobFieldValue.job_id == job.id,
                JobFieldValue.row_index.isnot(None),
                JobFieldValue.label_name == label).all():
            out[((fv.set_index or 1), fv.row_index)] = (
                fv.corrected_value or fv.extracted_value or "").strip()
        return out

    for cf in custom_fields:
        if getattr(cf, "per_row", False):
            if line_count == 0:
                logger.warning(
                    "per-row field %r on job %s: no line items were found, so no slots created",
                    cf.label_name, job.id,
                )
            looked_up: dict[tuple[int, int], str] = {}
            if cf.kind == "lookup":
                # Loaded per field, because two fields can ask the same sheet different
                # questions - the CTH from one column, a duty rate from another. Parsing is
                # cached on the file and the chosen columns, so this costs nothing after the
                # first job.
                master = load_master(
                    group.id,
                    match_columns=getattr(cf, "lookup_match_columns", None),
                    return_column=getattr(cf, "lookup_return_column", None),
                )
                # Key off the named line field, or the line's material code, or the leading
                # token of its description - which is where these invoices put the part number.
                key_label = (getattr(cf, "lookup_key_label", None) or "").strip()
                keys = _line_values(key_label) if key_label else {}
                codes = keys or _line_values("item_material_code")
                descs = _line_values("product_description")
                hits = 0
                cache_hits = 0
                for key in line_keys:
                    desc = descs.get(key, "")
                    material = material_from(codes.get(key, ""), desc)
                    # The part number first, then the description - the customer's list is
                    # keyed on both, and a line without a code still has words on it.
                    code = lookup_cth(material, master, description=desc)
                    if code:
                        hits += 1
                    elif material or desc:
                        # The dump has nothing for this line - but an operator may already have
                        # typed this exact material/description once before, on this job or an
                        # earlier one, and that answer was kept rather than asked for twice.
                        cached = lookup_reference(db, cf.id, [material, desc])
                        if cached:
                            code = cached
                            cache_hits += 1
                    looked_up[key] = code
                logger.info("%r on job %s: %s of %s line(s) found in the reference sheet, %s more "
                            "from previously-learned values (%s keys, returning %r)",
                            cf.label_name, job.reference, hits, line_count, cache_hits, len(master),
                            getattr(cf, "lookup_return_column", None) or "the code column")
            elif cf.kind == "ai" and line_count > 0:
                # A per-row field computed from a PROMPT rather than a lookup sheet or a fixed
                # value - e.g. a CTH actually PRINTED on a document for that specific line, as
                # opposed to one looked up from a reference sheet. Built from this job's own
                # real product lines (their part code/description/quantity, whatever this
                # job's own extraction already found), in the SAME row order, so a blank
                # answer stays that line's answer rather than silently shifting every later
                # line's value up by one row - see compute_custom_field_per_row's own
                # docstring for why that distinction matters specifically for a field like
                # this one. One batched call for the whole field, not one call per row, same
                # as every other AI-computed field here.
                from app.core.llm import compute_custom_field_per_row

                src_ids = cf.source_document_ids or [d.id for d in group.documents]

                def _files_in_row(did: str) -> int:
                    return max(1, len([d for d in docs_by_tdoc.get(did, []) if d.file_path]))

                n_files = sum(_files_in_row(did) for did in src_ids)
                share = max(2000, RULING_TEXT_BUDGET // max(1, n_files))
                chunks = []
                for did in src_ids:
                    name = next((d.name for d in group.documents if d.id == did), "DOC")
                    text = text_for(did)
                    allow = share * _files_in_row(did)
                    if len(text) > allow:
                        text = text[:allow] + "\n…[document truncated]"
                    chunks.append(f"=== {name} ===\n{text}")
                docs_text = "\n\n".join(chunks)
                chosen = set(src_ids)
                records = [r for r in doc_records if r["template_document_id"] in chosen]

                descs = _line_values("product_description")
                codes = _line_values("item_material_code")
                qtys = _line_values("item_quantity")
                row_context = [
                    {
                        "row": i + 1,
                        "part_code": codes.get(key, ""),
                        "description": descs.get(key, ""),
                        "quantity": qtys.get(key, ""),
                    }
                    for i, key in enumerate(line_keys)
                ]
                answers = compute_custom_field_per_row(
                    cf.ai_prompt or "", docs_text, row_context, records=records,
                )
                for key, answer in zip(line_keys, answers):
                    looked_up[key] = answer
            elif cf.kind == "composite":
                # Pure join over data this job already has for the line - no prompt, no
                # reference sheet. Each piece is either another field's label_name (a mark
                # like item_material_code/product_description, or another custom field
                # already computed earlier this same run - see the sort above) or a fixed
                # literal value typed straight in - see _composite_piece_value. Blank pieces
                # are skipped entirely rather than leaving a stray double space where a line
                # has nothing for one piece. The actual pieces used come from this job's own
                # consignee if that consignee has used this field before - see
                # _effective_composite_pieces.
                pieces_cfg = _effective_composite_pieces(db, job, cf)
                field_labels = [p for p in pieces_cfg if isinstance(p, str)]
                piece_values = {label: _line_values(label) for label in field_labels}
                for key in line_keys:
                    pieces = [_composite_piece_value(p, piece_values, key) for p in pieces_cfg]
                    looked_up[key] = " ".join(p for p in pieces if p)
            for key in line_keys:
                value = looked_up.get(key) or cf.hardcoded_value or ""
                # Idempotent on purpose: the top-of-function delete means this is normally a
                # fresh insert every time, but a second run_extraction racing this one (closed
                # off at its source in _maybe_auto_extract, but defended here too - see that
                # function's own comment) could otherwise have already committed this exact
                # (field, line) row by the time this one reaches it. Refresh in place instead
                # of duplicating rather than trust that can never happen.
                existing = (
                    db.query(JobFieldValue)
                    .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                            JobFieldValue.set_index == key[0], JobFieldValue.row_index == key[1])
                    .first()
                )
                if existing is not None:
                    existing.extracted_value = value
                    existing.label_name = cf.label_name
                else:
                    db.add(
                        JobFieldValue(
                            tenant_id=job.tenant_id,
                            job_id=job.id,
                            custom_field_id=cf.id,
                            label_name=cf.label_name,
                            extracted_value=value,
                            set_index=key[0],
                            row_index=key[1],
                        )
                    )
            # Autoflush is off on this session (see SessionLocal) - without an explicit flush
            # here, a composite field processed later in this same loop would query for this
            # field's label via _line_values and see nothing, because these adds are still only
            # pending. A composite field is the first kind that genuinely reads ANOTHER custom
            # field's value from the same run rather than just a mark's.
            db.flush()
            continue
        if cf.kind == "hardcoded":
            value = cf.hardcoded_value or ""
        else:
            src_ids = cf.source_document_ids or [d.id for d in group.documents]
            # Same fair share as the ruling: compute_custom_field truncates the bundle, so a
            # field sourced from several documents would let the bill of lading's 42,000
            # characters — mostly page-2 carrier boilerplate — swallow the allowance and hide
            # every other document. A field told to decide "from all the documents" must
            # actually receive all of them.
            # An equal share per FILE, not per slot. Dividing by the slot count gave the
            # Invoice slot one invoice's worth of room no matter how many invoices were in
            # it, so a field told to total three invoices could only ever see the first.
            def _files_in(did: str) -> int:
                return max(1, len([d for d in docs_by_tdoc.get(did, []) if d.file_path]))

            n_files = sum(_files_in(did) for did in src_ids)
            share = max(2000, RULING_TEXT_BUDGET // max(1, n_files))
            chunks = []
            for did in src_ids:
                name = next((d.name for d in group.documents if d.id == did), "DOC")
                text = text_for(did)
                allow = share * _files_in(did)
                if len(text) > allow:
                    text = text[:allow] + "\n…[document truncated]"
                chunks.append(f"=== {name} ===\n{text}")
            docs_text = "\n\n".join(chunks)

            # The records for the documents this field was told to read. Complete and never
            # shortened — all_values could only ever carry the FIRST document's value per
            # label, so a field asked to total three invoices was totalling one.
            chosen = set(src_ids)
            records = [r for r in doc_records if r["template_document_id"] in chosen]

            if cf.kind == "ai" and getattr(cf, "multi_value_from_document", False):
                # Same idea as a Mark ticked "multiple values in this document" — instead of
                # one value for the whole field, the AI reads its own document(s) and returns
                # however many it actually finds there (every container number on the bill of
                # lading, one row per line on a packing list with no mark on it, etc). One
                # JobFieldValue per row, same as a multi-value mark, but numbered on its own —
                # this field was never tied to any mark's line items to begin with.
                from app.core.llm import compute_custom_field_rows

                rows = compute_custom_field_rows(cf.ai_prompt or "", docs_text, records=records)
                logger.info("%r on job %s: %d row(s) read directly from its own document(s)",
                            cf.label_name, job.reference, len(rows))
                # Idempotent on the same reasoning as every other write loop in this function -
                # see the per-row custom field loop's own comment for why.
                for i, row_value in enumerate(rows, start=1):
                    existing_row = (
                        db.query(JobFieldValue)
                        .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                                JobFieldValue.row_index == i)
                        .first()
                    )
                    if existing_row is not None:
                        existing_row.extracted_value = row_value
                        existing_row.label_name = cf.label_name
                    else:
                        db.add(
                            JobFieldValue(
                                tenant_id=job.tenant_id,
                                job_id=job.id,
                                custom_field_id=cf.id,
                                label_name=cf.label_name,
                                extracted_value=row_value,
                                row_index=i,
                            )
                        )
                continue

            from app.core.llm import compute_custom_field

            value = compute_custom_field(cf.ai_prompt or "", docs_text, records=records)
        value, raw_value = _resolve_target_value(db, job.id, cf, value)
        # Idempotent on the same reasoning as every other write loop in this function - see
        # the per-row custom field loop's own comment for why.
        existing_cf_value = (
            db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                    JobFieldValue.row_index.is_(None))
            .first()
        )
        if existing_cf_value is not None:
            existing_cf_value.label_name = cf.label_name
            existing_cf_value.extracted_value = value
            existing_cf_value.target_value_raw = raw_value if getattr(cf, "is_target_value", False) else None
        else:
            db.add(
                JobFieldValue(
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    custom_field_id=cf.id,
                    label_name=cf.label_name,
                    extracted_value=value,
                    target_value_raw=raw_value if getattr(cf, "is_target_value", False) else None,
                )
            )
        # Flushed per field, not left to whatever the session's own autoflush setting
        # happens to be - a lookup_key_label field sorted to run right after the field it
        # depends on (see the sort above) still needs that field's row to actually be
        # QUERYABLE, not merely pending, the moment its own turn comes.
        db.flush()

    # Make sure every uploaded document's raw OCR is captured, including ones with no
    # marks of their own (a document can exist purely to be cross-checked against).
    for tdoc in group.documents:
        for jd in docs_by_tdoc.get(tdoc.id, []):
            if jd.file_path and jd.extracted_json is None:
                text_for_doc(jd)

    # Assemble the keyed-out snapshot: {document -> {label: value}}, with the computed and
    # hardcoded fields under "Custom". job_field_values stays the source of truth; this is
    # the ready-to-export view so consumers need not re-join rows.
    db.flush()
    # With several invoices on one job the snapshot keeps them APART — "Invoice (set 1)",
    # "Invoice (set 2)" — rather than folding three invoice numbers into one key where two of
    # them would be lost. A job with a single set is unchanged and still reads "Invoice".
    multi_set = len({d.set_index for lst in docs_by_tdoc.values() for d in lst
                     if d.file_path and d.set_index}) > 1
    keyouted: dict[str, dict] = {}
    for fv in sorted(
        db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all(),
        key=lambda f: (f.set_index or 0, f.label_name, f.row_index or 0),
    ):
        if old_corrections:
            restored = old_corrections.get(_correction_key(fv))
            if restored is not None:
                fv.corrected_value = restored
        if fv.custom_field_id:
            bucket = "Custom"
        else:
            tdoc = tdoc_by_id.get(fv.template_document_id)
            bucket = tdoc.name if tdoc else "Unknown"
            if multi_set and fv.set_index:
                bucket = f"{bucket} (set {fv.set_index})"
        if fv.row_index is None:
            keyouted.setdefault(bucket, {})[fv.label_name] = fv.value
        else:
            # Line-item field: an ordered list, element 0 = row 1.
            keyouted.setdefault(bucket, {}).setdefault(fv.label_name, []).append(fv.value)
    job.extracted_keyouted_data = keyouted
    job.content_checksum = _compute_content_checksum(keyouted)

    existing_dup = _find_duplicate_job(db, job)
    if existing_dup is not None:
        job.duplicate_of_job_id = existing_dup.id
        job.status = "possible_duplicate"
        db.commit()
        db.refresh(job)
        logger.info("job %s: flagged as a possible duplicate of %s (matching content checksum)",
                    job.reference, existing_dup.reference)
        return

    job.status = "extracted"
    db.commit()
    db.refresh(job)

    # Now that every document has been read, let the ruling decide whether this job can
    # proceed — or must wait on the operator. Covers both the Extract button and jobs
    # created by the email puller, since both land here.
    apply_ruling_hold(db, job)
    db.refresh(job)


@router.post("/jobs/{job_id}/extract", response_model=JobDetailOut)
def extract_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN)),
) -> JobDetailOut:
    """Manual Extract / Re-run extraction. Backgrounded the same way the auto-trigger is, so
    pressing it does not sit the operator's browser on OCR + model calls — the response comes
    back at once with status "extracting", and the screen polls until it settles."""
    job = _load_job(db, job_id, scope, user)
    if is_extraction_paused(db):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Extraction is currently paused by an administrator.")
    _begin_extraction(db, job)
    db.refresh(job)
    return _build_detail(db, job)


@router.post("/jobs/{job_id}/duplicate-decision", response_model=JobDetailOut)
def resolve_duplicate(
    job_id: str,
    payload: DuplicateDecision,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN)),
) -> JobDetailOut:
    """The operator looked at a job flagged "possible_duplicate" (its extracted data matched
    an existing job's, see _compute_content_checksum) and decided it is genuinely a separate
    shipment - let it proceed exactly as if it had never been flagged. `duplicate_of_job_id`
    is kept as the audit trail of what it was flagged against.

    "Delete" is not handled here - the popup's Delete button calls the existing
    DELETE /jobs/{job_id}, the same one used everywhere else a job is removed. "Hold" needs no
    call at all: the job simply stays "possible_duplicate" until someone decides.
    """
    job = _load_job(db, job_id, scope, user)
    if job.status != "possible_duplicate":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This job is not flagged as a possible duplicate.")
    if payload.decision != "approve":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail='Only "approve" is handled here — see Delete/Hold above.')
    job.status = "extracted"
    db.commit()
    db.refresh(job)
    apply_ruling_hold(db, job)
    db.refresh(job)
    return _build_detail(db, job)


def _line_key_values(db: Session, job_id: str, set_index: int | None, row_index: int | None,
                      key_label: str | None = None) -> tuple[str, str]:
    """The (material, description) pair for one line item - the same two values a kind="lookup"
    custom field is matched against at extraction time (see run_extraction above), so a
    correction can be learned against the identical key a future job's extraction will look it
    up with."""
    from app.core.material_master import material_from

    if row_index is None:
        return "", ""
    labels = {"product_description", "item_material_code"}
    if key_label:
        labels.add(key_label)
    rows = db.query(JobFieldValue.label_name, JobFieldValue.corrected_value,
                     JobFieldValue.extracted_value).filter(
        JobFieldValue.job_id == job_id,
        JobFieldValue.set_index == (set_index or 1),
        JobFieldValue.row_index == row_index,
        JobFieldValue.label_name.in_(labels),
    ).all()
    values = {label: (corrected or extracted or "").strip() for label, corrected, extracted in rows}
    desc = values.get("product_description", "")
    code = (values.get(key_label, "") if key_label else "") or values.get("item_material_code", "")
    return material_from(code, desc), desc


@router.patch("/job-field-values/{value_id}", response_model=JobFieldValueOut)
def correct_field_value(
    value_id: str,
    payload: FieldValueCorrect,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> JobFieldValueOut:
    fv = scoped_query(db, JobFieldValue, scope).filter(JobFieldValue.id == value_id).first()
    if fv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field value not found")
    # A value on its own carries no owner or mode - only its job does. Loading the job through
    # the shared helper is what actually enforces an operator's own-job restriction and a
    # GK2 user's assigned_modes; skipping it here (this used to discard `user` entirely) meant
    # either restriction could be bypassed just by knowing/guessing a value's id.
    _load_job(db, fv.job_id, scope, user)
    fv.corrected_value = payload.corrected_value
    # A kind="lookup" field the master had nothing for, now filled in by hand: remember it, so
    # the same material/description on a later line - this job or any other - is filled in
    # automatically instead of asked for again. Never for a value the master DID already find:
    # that would let a one-off manual override of a correct dump answer poison every future line
    # that legitimately matches the dump.
    if fv.custom_field_id and not (fv.extracted_value or "").strip():
        from app.core.reference_cache import remember_reference
        from app.models.custom_field import CustomField

        cf = db.get(CustomField, fv.custom_field_id)
        if cf is not None and cf.kind == "lookup":
            material, desc = _line_key_values(
                db, fv.job_id, fv.set_index, fv.row_index, key_label=cf.lookup_key_label)
            remember_reference(db, cf.id, [material, desc], payload.corrected_value or "")
    # is_target_value (mark or custom field): the reference table had nothing for what this
    # field actually extracted (target_value_raw), now filled in by hand - remember it against
    # THAT raw value, so the same raw extraction is resolved automatically next time instead of
    # coming back empty again. Never when a match WAS already found (extracted_value is
    # non-blank): that would let a one-off manual override poison every future match. A plain
    # "if", not "elif" off the kind="lookup" branch above - that branch's own OUTER condition
    # (any custom field with a blank extraction) is a superset of this one and would always
    # win first, silently skipping this remember for every target-value CUSTOM field (a mark
    # has no custom_field_id at all, so it was the only case this ever actually ran for).
    if fv.target_value_raw and not (fv.extracted_value or "").strip():
        from app.core.reference_cache import remember_reference

        fuzzy = False
        if fv.custom_field_id:
            from app.models.custom_field import CustomField as _CF
            owner_cf = db.get(_CF, fv.custom_field_id)
            fuzzy = bool(owner_cf and owner_cf.fuzzy_match)
        elif fv.mark_id:
            mark = db.get(FieldMark, fv.mark_id)
            fuzzy = bool(mark and mark.fuzzy_match)
        remember_reference(
            db, custom_field_id=fv.custom_field_id, mark_id=fv.mark_id,
            match_values=[fv.target_value_raw], resolved_value=payload.corrected_value or "",
            fuzzy=fuzzy,
        )
    db.commit()
    db.refresh(fv)
    tdoc = db.get(TemplateDocument, fv.template_document_id) if fv.template_document_id else None
    mark = db.get(FieldMark, fv.mark_id) if fv.mark_id else None
    return JobFieldValueOut(
        id=fv.id,
        mark_id=fv.mark_id,
        template_document_id=fv.template_document_id,
        document_name="✨ Custom" if fv.custom_field_id else (tdoc.name if tdoc else "?"),
        label_name=fv.label_name,
        extracted_value=fv.extracted_value,
        corrected_value=fv.corrected_value,
        value=fv.value,
        is_custom=bool(fv.custom_field_id),
        mark_page=mark.page_number if mark else None,
        mark_x=mark.x if mark else None,
        mark_y=mark.y if mark else None,
        mark_width=mark.width if mark else None,
        mark_height=mark.height if mark else None,
        # Unchanged by a correction — this is where the ORIGINAL extraction's reading was
        # found, still worth showing while cross-checking a manual correction against it.
        found_page=fv.found_page,
        found_x=fv.found_x,
        found_y=fv.found_y,
        found_width=fv.found_width,
        found_height=fv.found_height,
    )


def _recompute_per_row_custom_field(
    db: Session, job: Job, cf: "CustomField", composite_pieces_override: list | None = None,
) -> list[JobFieldValueOut]:
    """The per-row half of recompute_custom_field's own logic, pulled out so a second,
    narrower entry point (an operator picking a composite field's piece order from the job
    screen - see composite_fields_for_job/set_composite_field_order below) can reuse it
    without duplicating it, rather than only ever being reachable from that endpoint.

    composite_pieces_override (kind="composite" only): compute THIS job with exactly these
    pieces instead of resolving them from the consignee/generic default - set_composite_field_order
    uses this so pressing Apply immediately reflects what was just chosen (fixed-value pieces
    included) even though only the field-reference pieces of it get remembered for the
    consignee going forward - see _effective_composite_pieces.
    """
    line_keys: list[tuple[int, int]] = sorted({
        ((r[0] or 1), r[1])
        for r in db.query(JobFieldValue.set_index, JobFieldValue.row_index)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.isnot(None))
        .distinct().all()
    })
    if not line_keys:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This job has no line items yet — a per-row field has nothing to backfill into.")

    # A per-row field's VALUE depends on its own kind exactly the way run_extraction's own
    # per-row loop does - a lookup field reads the reference sheet, an AI field reads the
    # document(s) it was told to, keyed to this job's own real product lines; only a
    # genuinely hardcoded field has one fixed answer for every line. This endpoint used to
    # apply `cf.hardcoded_value` unconditionally regardless of kind, so recomputing a
    # per-row lookup or AI field here (e.g. a CTH) silently blanked every line instead of
    # actually recomputing it - found while wiring up a second per-row AI field and
    # confirming this same endpoint would need to handle it too.
    def _row_values(label: str) -> dict[tuple[int, int], str]:
        out: dict[tuple[int, int], str] = {}
        for fv in db.query(JobFieldValue).filter(
                JobFieldValue.job_id == job.id,
                JobFieldValue.row_index.isnot(None),
                JobFieldValue.label_name == label).all():
            out[((fv.set_index or 1), fv.row_index)] = (
                fv.corrected_value or fv.extracted_value or "").strip()
        return out

    computed: dict[tuple[int, int], str] = {}
    if cf.kind == "lookup":
        from app.core.material_master import load_master, lookup_cth, material_from
        from app.core.reference_cache import lookup_reference

        master = load_master(
            job.group_id,
            match_columns=getattr(cf, "lookup_match_columns", None),
            return_column=getattr(cf, "lookup_return_column", None),
        )
        key_label = (getattr(cf, "lookup_key_label", None) or "").strip()
        keys = _row_values(key_label) if key_label else {}
        codes = keys or _row_values("item_material_code")
        descs = _row_values("product_description")
        for key in line_keys:
            desc = descs.get(key, "")
            material = material_from(codes.get(key, ""), desc)
            code = lookup_cth(material, master, description=desc)
            if not code and (material or desc):
                code = lookup_reference(db, cf.id, [material, desc]) or ""
            computed[key] = code
    elif cf.kind == "ai":
        from app.core.llm import compute_custom_field_per_row

        group = db.get(TemplateGroup, job.group_id)
        docs_by_tdoc: dict[str, list[JobDocument]] = {}
        for d in (db.query(JobDocument).filter(JobDocument.job_id == job.id)
                  .order_by(JobDocument.file_index).all()):
            docs_by_tdoc.setdefault(d.template_document_id, []).append(d)

        src_ids = cf.source_document_ids or [d.id for d in group.documents]
        chunks = []
        for did in src_ids:
            tdoc = next((d for d in group.documents if d.id == did), None)
            name = tdoc.name if tdoc else "DOC"
            for jdoc in docs_by_tdoc.get(did, []):
                if not jdoc.file_path:
                    continue
                cached = jdoc.extracted_json
                text = cached.get("text", "") if cached else ""
                chunks.append(f"=== {name} ===\n{text}")
        docs_text = "\n\n".join(chunks)
        records = _document_records(db, job, group)
        chosen = set(src_ids)
        records = [r for r in records if r["template_document_id"] in chosen]

        descs = _row_values("product_description")
        codes = _row_values("item_material_code")
        qtys = _row_values("item_quantity")
        row_context = [
            {
                "row": i + 1,
                "part_code": codes.get(key, ""),
                "description": descs.get(key, ""),
                "quantity": qtys.get(key, ""),
            }
            for i, key in enumerate(line_keys)
        ]
        answers = compute_custom_field_per_row(cf.ai_prompt or "", docs_text, row_context, records=records)
        computed = dict(zip(line_keys, answers))
    elif cf.kind == "composite":
        pieces_cfg = (
            composite_pieces_override if composite_pieces_override is not None
            else _effective_composite_pieces(db, job, cf)
        )
        field_labels = [p for p in pieces_cfg if isinstance(p, str)]
        piece_values = {label: _row_values(label) for label in field_labels}
        for key in line_keys:
            pieces = [_composite_piece_value(p, piece_values, key) for p in pieces_cfg]
            computed[key] = " ".join(p for p in pieces if p)

    rows: list[JobFieldValue] = []
    for set_index, row_index in line_keys:
        value = computed.get((set_index, row_index), cf.hardcoded_value or "")
        existing = (
            db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                    JobFieldValue.set_index == set_index, JobFieldValue.row_index == row_index)
            .first()
        )
        if existing is None:
            existing = JobFieldValue(
                tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cf.id,
                label_name=cf.label_name, set_index=set_index, row_index=row_index,
            )
            db.add(existing)
        existing.label_name = cf.label_name
        existing.extracted_value = value
        rows.append(existing)
    # A field turned per_row AFTER it already had a single job-level answer (row_index
    # None) leaves that old slot stranded once the per-line ones above take over - the
    # operator would see the SAME field twice, once in "For the whole job" and once per
    # line. Safe to drop only when nobody actually typed an answer into it; a real
    # correction has no single line to fall back into automatically, so it is left alone
    # rather than silently discarded.
    stale = (db.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                    JobFieldValue.row_index.is_(None))
            .first())
    if stale is not None and not (stale.corrected_value or "").strip():
        db.delete(stale)
    db.commit()
    results = []
    for row in rows:
        db.refresh(row)
        results.append(JobFieldValueOut(
            id=row.id, mark_id=None, template_document_id=None, document_name="✨ Custom",
            label_name=row.label_name, extracted_value=row.extracted_value,
            corrected_value=row.corrected_value, value=row.value, is_custom=True,
            origin="computed", set_index=row.set_index, row_index=row.row_index,
        ))
    return results


@router.post("/jobs/{job_id}/custom-fields/{custom_field_id}/recompute", response_model=list[JobFieldValueOut])
def recompute_custom_field(
    job_id: str,
    custom_field_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> list[JobFieldValueOut]:
    """Backfill ONE custom field on ONE already-extracted job — nothing else on the job is
    touched.

    Built for a field added to a template AFTER a job was already extracted: re-running
    /extract would answer that, but it also rewrites every OTHER field on the job, resets
    every approval, and kicks the job back to Data Extraction — unacceptable on a job
    someone has already reviewed, approved, or submitted. Ordinarily writes exactly one
    JobFieldValue — job-level, row_index=None. Job status, stage, approvals and every
    other field value are left exactly as they were.

    kind="ai": reuses each document's ALREADY-CACHED OCR (the same on-disk cache
    get_page_ocr checks before ever calling Document AI again, or the text already saved
    on job_documents.extracted_json) rather than reading anything fresh.

    kind="hardcoded": no document to read at all - this is exactly how a "Manual Entry"
    field from the ERP Script Recorder shows up. Without this, a field added there stayed
    invisible on Additional Details for every job extracted before it existed, with no way
    to backfill it short of a full re-extraction.

    per_row=True: a field switched to per-row AFTER a job was already extracted still had
    only its one old job-level slot — the operator saw a single box instead of one per
    product line, with no way to fill the rest short of a full re-extraction. Backfills one
    JobFieldValue per (set_index, row_index) the job's own line-item marks already
    established — same keys, same "hardcoded/kind='ai' non-lookup falls back to
    hardcoded_value" rule run_extraction's own per-row loop uses — so this never drifts
    from what a fresh extraction would have written.
    """
    from app.models.custom_field import CustomField

    job = _load_job(db, job_id, scope, user)
    cf = db.get(CustomField, custom_field_id)
    if cf is None or cf.group_id != job.group_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found on this job's template.")
    if cf.kind not in ("ai", "hardcoded", "composite"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail='Only an AI-computed, hardcoded, or composite field can be recomputed — a per-row lookup field is not read off a document.')
    if cf.kind == "composite" and not getattr(cf, "per_row", False):
        # A composite field only makes sense per line (it is a join of OTHER line fields);
        # a job-level one has no per-row pieces to read and no per-row AI prompt to fall
        # back to either, so recomputing it would silently run the wrong code path below.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A composite field must be per-row.")

    if getattr(cf, "per_row", False):
        return _recompute_per_row_custom_field(db, job, cf)

    if cf.kind == "hardcoded":
        value, raw_value = _resolve_target_value(db, job.id, cf, cf.hardcoded_value or "")
    else:
        group = db.get(TemplateGroup, job.group_id)
        docs_by_tdoc: dict[str, list[JobDocument]] = {}
        for d in (db.query(JobDocument).filter(JobDocument.job_id == job.id)
                  .order_by(JobDocument.file_index).all()):
            docs_by_tdoc.setdefault(d.template_document_id, []).append(d)
        custom_filter_texts = get_active_custom_filter_texts(db)

        def text_for_doc(jdoc: JobDocument, tdoc_name: str) -> str:
            # Reuse whatever run_extraction already saved rather than re-reading anything, if
            # it is there.
            cached = jdoc.extracted_json
            if cached and cached.get("text") is not None:
                return cached["text"]
            parts: list[str] = []
            if jdoc.file_path:
                for page in range(1, jdoc.page_count + 1):
                    try:
                        page_ocr = get_page_ocr(_job_doc_dir(jdoc.id), page)
                        parts.append(
                            (get_settings().structure_engine_enabled and page_ocr.get("structured_text"))
                            or page_ocr.get("layout_text")
                            or page_ocr.get("text", "")
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("OCR unavailable for job doc %s page %d during recompute", jdoc.id, page)
            kept, dropped_pages = filter_pages_with_custom(parts, custom_filter_texts, tdoc_name)
            text = "\n".join(kept)
            if parts:
                jdoc.extracted_json = {
                    "document": tdoc_name, "page_count": len(parts),
                    "pages": parts, "excluded_pages": dropped_pages, "text": text,
                }
            return text

        src_ids = cf.source_document_ids or [d.id for d in group.documents]
        chunks = []
        for did in src_ids:
            tdoc = next((d for d in group.documents if d.id == did), None)
            name = tdoc.name if tdoc else "DOC"
            for jdoc in docs_by_tdoc.get(did, []):
                if not jdoc.file_path:
                    continue
                chunks.append(f"=== {name} ===\n{text_for_doc(jdoc, name)}")
        docs_text = "\n\n".join(chunks)

        # Every uploaded document's own record, same as a fresh extraction's records pass - a
        # field scoped to only some documents still benefits from seeing all of them
        # cross-referenced.
        records = _document_records(db, job, group)

        from app.core.llm import compute_custom_field

        value = compute_custom_field(cf.ai_prompt or "", docs_text, records=records)
        value, raw_value = _resolve_target_value(db, job.id, cf, value)

    existing = (db.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id,
                        JobFieldValue.row_index.is_(None))
                .first())
    if existing is None:
        existing = JobFieldValue(tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name)
        db.add(existing)
    # Synced on every recompute, not just at creation — a field renamed after this job was
    # already extracted must not keep showing the operator its old name forever.
    existing.label_name = cf.label_name
    existing.extracted_value = value
    existing.target_value_raw = raw_value if getattr(cf, "is_target_value", False) else None
    db.commit()
    db.refresh(existing)
    return [JobFieldValueOut(
        id=existing.id, mark_id=None, template_document_id=None, document_name="✨ Custom",
        label_name=existing.label_name, extracted_value=existing.extracted_value,
        corrected_value=existing.corrected_value, value=existing.value, is_custom=True,
        origin="computed",
    )]


class CompositeFieldOut(BaseModel):
    id: str
    label_name: str
    # Each piece is either another field's label_name (a string) or a fixed literal value
    # typed straight in ({"fixed": "<text>"}) - see _composite_piece_value's own docstring.
    composite_source_labels: list[str | dict[str, str]]


class CompositeFieldsOut(BaseModel):
    # Every already-existing per-line field (mark or custom field) this job's template could
    # combine into one - a Super Admin-free view of just enough of the template to build the
    # picker, never its prompts/hardcoded values/reference sheet setup.
    available_labels: list[str]
    existing: CompositeFieldOut | None = None


@router.get("/jobs/{job_id}/composite-fields", response_model=CompositeFieldsOut)
def composite_fields_for_job(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> CompositeFieldsOut:
    """What this job's own "Combine fields" button (Product Detail, job screen) has to offer -
    see set_composite_field_order below for what pressing Apply there actually does."""
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument

    job = _load_job(db, job_id, scope, user)
    mark_labels = [
        m.label_name for m in db.query(FieldMark)
        .join(TemplateDocument, TemplateDocument.id == FieldMark.document_id)
        .filter(TemplateDocument.group_id == job.group_id, FieldMark.is_multi_value.is_(True)).all()
    ]
    cf_rows = db.query(CustomField).filter(CustomField.group_id == job.group_id).all()
    # Only a per-row custom field that ACTUALLY shows on the Product Detail card - the same
    # rule the frontend's own lookedUpByRow uses (self_filled, i.e. kind="lookup"/"composite",
    # or paired with one). A plain ask_operator per-row field with no pairing (a duty
    # notification number, say) never appears there at all - only on Additional Details - so it
    # has no business being offered as a "piece" here either.
    field_labels = [
        c.label_name for c in cf_rows
        if c.per_row and (c.kind in ("lookup", "composite") or c.paired_custom_field_id)
    ]
    composite_rows = [c for c in cf_rows if c.kind == "composite" and c.per_row]
    existing_cf = (
        next((c for c in composite_rows if c.label_name == "Combined Description"), None)
        or (composite_rows[0] if composite_rows else None)
    )
    available = sorted(set(mark_labels) | set(field_labels))
    if existing_cf is not None:
        # A composite field can't usefully combine ITSELF - offering its own label as a
        # choosable piece would let an operator build a circular reference.
        available = [l for l in available if l != existing_cf.label_name]
    existing = None
    if existing_cf is not None:
        # Pre-fill with THIS job's own consignee's remembered order when they have one,
        # rather than always showing the template's generic fallback - see
        # _effective_composite_pieces (the same resolution the actual computation uses).
        existing = CompositeFieldOut(
            id=existing_cf.id, label_name=existing_cf.label_name,
            composite_source_labels=_effective_composite_pieces(db, job, existing_cf),
        )
    return CompositeFieldsOut(available_labels=available, existing=existing)


class CompositeFieldUpdate(BaseModel):
    label_name: str = "Combined Description"
    # Each piece is either another field's label_name (a string) or a fixed literal value
    # typed straight in ({"fixed": "<text>"}) - see _composite_piece_value's own docstring.
    source_labels: list[str | dict[str, str]]


@router.put("/jobs/{job_id}/composite-fields", response_model=list[JobFieldValueOut])
def set_composite_field_order(
    job_id: str,
    payload: CompositeFieldUpdate,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2)),
) -> list[JobFieldValueOut]:
    """Let anyone reviewing a job set the piece order for the template's composite field, from
    the job screen itself rather than the Super Admin wizard.

    composite_source_labels on the CustomField itself stays the template-wide GENERIC
    fallback - unchanged behaviour, still updated on every Apply. On top of that, if this
    job has a consignee (consignee_full_name), the field-reference pieces (never a fixed
    value - see CompositeFieldConsigneeDefault's own docstring) are also remembered for THAT
    consignee specifically: any later job for the SAME consignee resolves (both for display
    and for computing its real value) to their own remembered order first, the generic
    fallback only when a consignee has none of their own yet - see
    _effective_composite_pieces. THIS job is always recomputed with exactly what was just
    chosen here (fixed values included), regardless of which pieces get remembered for next
    time. Deliberately narrow otherwise: this never touches kind, prompts, hardcoded_value, or
    any other admin-only config - only composite_source_labels (and per_row/kind/label_name on
    first creation) - so an operator can reorder pieces here but not rewrite the field into
    something else entirely.
    """
    from app.models.composite_consignee_default import CompositeFieldConsigneeDefault
    from app.models.custom_field import CustomField

    job = _load_job(db, job_id, scope, user)
    label = payload.label_name.strip()
    if not label:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Give the field a label.")
    if not payload.source_labels:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose at least one piece to combine.")
    for piece in payload.source_labels:
        if isinstance(piece, dict) and not (piece.get("fixed") or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="A fixed-value piece cannot be empty.")

    composite_rows = (
        db.query(CustomField)
        .filter(CustomField.group_id == job.group_id, CustomField.kind == "composite",
                CustomField.per_row.is_(True))
        .all()
    )
    cf = next((c for c in composite_rows if c.label_name == label), None) or (
        composite_rows[0] if composite_rows else None
    )
    if cf is None:
        cf = CustomField(
            tenant_id=job.tenant_id, group_id=job.group_id,
            label_name=label, kind="composite", per_row=True,
        )
        db.add(cf)
    if any(isinstance(p, str) and p == cf.label_name for p in payload.source_labels):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A field cannot combine itself.")
    cf.composite_source_labels = payload.source_labels
    db.flush()  # cf.id must exist (a brand-new field) before it can be a foreign key below.

    consignee_key = _job_consignee_key(db, job)
    if consignee_key:
        # Only the field-reference pieces are remembered per consignee - never a fixed value,
        # which is not assumed to be the same text on this consignee's NEXT job.
        field_only = [p for p in payload.source_labels if isinstance(p, str)]
        default_row = (
            db.query(CompositeFieldConsigneeDefault)
            .filter(CompositeFieldConsigneeDefault.custom_field_id == cf.id,
                    CompositeFieldConsigneeDefault.consignee_key == consignee_key)
            .first()
        )
        if default_row is None:
            default_row = CompositeFieldConsigneeDefault(
                tenant_id=job.tenant_id, custom_field_id=cf.id, consignee_key=consignee_key,
                source_labels=field_only,
            )
            db.add(default_row)
        else:
            default_row.source_labels = field_only

    db.commit()
    db.refresh(cf)
    return _recompute_per_row_custom_field(db, job, cf, composite_pieces_override=payload.source_labels)


@router.post("/jobs/{job_id}/marks/{mark_id}/recompute", response_model=list[JobFieldValueOut])
def recompute_mark(
    job_id: str,
    mark_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> list[JobFieldValueOut]:
    """Backfill ONE mark's value(s) on ONE already-extracted job, from its own already-
    uploaded document(s) — nothing else on the job is touched. Companion to
    recompute_custom_field (see there for why this exists): a mark's prompt was tightened
    AFTER a job was already extracted — excluding a freight forwarder that had been wrongly
    read as the real shipper/consignee, say — and re-running /extract to pick up that fix
    would rewrite every OTHER field on the job and reset every approval too.

    One value per uploaded FILE in this mark's own document slot (a slot holding three
    invoices gets three separate recomputed values, same as a fresh extraction would),
    each written to that file's own JobFieldValue row. Reuses get_page_ocr's own on-disk
    cache — cheap, no fresh Document AI call unless that cache is genuinely missing.

    Line-item (is_multi_value) marks are not supported here: recomputing one column of a
    table on its own would misalign the row it belongs to against its siblings.
    """
    job = _load_job(db, job_id, scope, user)
    mark = db.get(FieldMark, mark_id)
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")
    tdoc = db.get(TemplateDocument, mark.document_id)
    if tdoc is None or tdoc.group_id != job.group_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found on this job's template.")
    if mark.is_multi_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Line-item marks can't be recomputed individually — it would misalign the table's rows.")

    jdocs = (db.query(JobDocument)
             .filter(JobDocument.job_id == job.id, JobDocument.template_document_id == tdoc.id)
             .order_by(JobDocument.file_index).all())
    uploaded = [d for d in jdocs if d.file_path]
    if not uploaded:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This job has no uploaded document in that slot.")

    custom_filter_texts = get_active_custom_filter_texts(db)
    results: list[JobFieldValueOut] = []
    for jd in uploaded:
        parts: list[str] = []
        tokens_by_page: dict[int, list[dict]] = {}
        for page in range(1, jd.page_count + 1):
            try:
                page_ocr = get_page_ocr(_job_doc_dir(jd.id), page)
                parts.append(
                    (get_settings().structure_engine_enabled and page_ocr.get("structured_text"))
                    or page_ocr.get("layout_text")
                    or page_ocr.get("text", "")
                )
                if page_ocr.get("tokens"):
                    tokens_by_page[page] = page_ocr["tokens"]
            except Exception:  # noqa: BLE001
                logger.exception("OCR unavailable for job doc %s page %d during mark recompute", jd.id, page)
        kept, dropped_pages = filter_pages_with_custom(parts, custom_filter_texts, tdoc.name)
        ocr_text = "\n".join(kept)
        tokens = [(p, tokens_by_page[p]) for p in range(1, len(parts) + 1)
                  if p not in dropped_pages and p in tokens_by_page]

        used_vision = not ocr_text.strip()
        if not used_vision:
            extracted = extract_document_fields(ocr_text, [field_spec(mark)])
        else:
            image_paths = [_job_doc_dir(jd.id) / "pages" / f"page_{p}.png" for p in range(1, jd.page_count + 1)]
            image_paths = [p for p in image_paths if p.exists()]
            extracted = extract_document_fields_from_images(image_paths, [field_spec(mark)])
        val = extracted.get(mark.label_name)
        raw_val = val
        if mark.is_target_value and val:
            from app.core.reference_cache import lookup_reference

            resolved = lookup_reference(db, mark_id=mark.id, match_values=[val],
                                        fuzzy=getattr(mark, "fuzzy_match", False))
            val = resolved or None
        found = locate_value_bbox(tokens, val) if val else None

        existing = (db.query(JobFieldValue)
                    .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id,
                            JobFieldValue.job_document_id == jd.id, JobFieldValue.row_index.is_(None))
                    .first())
        if existing is None:
            existing = JobFieldValue(tenant_id=job.tenant_id, job_id=job.id, mark_id=mark.id,
                                     template_document_id=tdoc.id, job_document_id=jd.id,
                                     label_name=mark.label_name)
            db.add(existing)
        # Synced on every recompute, not just at creation — see the identical comment in
        # recompute_custom_field.
        existing.label_name = mark.label_name
        existing.extracted_value = val
        existing.target_value_raw = raw_val if mark.is_target_value else None
        existing.found_page = found[0] if found else None
        existing.found_x = found[1] if found else None
        existing.found_y = found[2] if found else None
        existing.found_width = found[3] if found else None
        existing.found_height = found[4] if found else None
        db.commit()
        db.refresh(existing)
        results.append(JobFieldValueOut(
            id=existing.id, mark_id=existing.mark_id, template_document_id=existing.template_document_id,
            document_name=tdoc.name, label_name=existing.label_name,
            extracted_value=existing.extracted_value, corrected_value=existing.corrected_value,
            value=existing.value, is_custom=False, job_document_id=existing.job_document_id,
            found_page=existing.found_page, found_x=existing.found_x, found_y=existing.found_y,
            found_width=existing.found_width, found_height=existing.found_height,
        ))
    return results


@router.post("/jobs/{job_id}/recompute-doc-sets")
def recompute_doc_sets(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Re-run ONLY the invoice/packing-list pairing (app/core/doc_sets.py) on an already-
    extracted job, using the field values it already has. No OCR, no AI call, and no actual
    extracted or corrected value is touched — only set_index, on the job's own documents and
    field values.

    Built to recover a job whose pairing went wrong before a doc_sets.py fix landed (the
    single-file-per-slot bug: a lone invoice and a lone packing list whose own "Invoice No"
    fields happened to read differently got split into two fake sets, so every Invoice-vs-
    Packing-List cross-check compared a real value against nothing on the other side) —
    without the cost and risk of a full /extract re-run, which would rewrite every field and
    discard any correction an operator already made.
    """
    from app.core.doc_sets import assign_sets, describe_sets, pairing_key

    job = _load_job(db, job_id, scope, user)
    group = db.get(TemplateGroup, job.group_id)
    docs = (db.query(JobDocument).filter(JobDocument.job_id == job.id)
            .order_by(JobDocument.file_index).all())
    docs_by_tdoc: dict[str, list[JobDocument]] = {}
    for d in docs:
        docs_by_tdoc.setdefault(d.template_document_id, []).append(d)

    field_values = (db.query(JobFieldValue)
                    .filter(JobFieldValue.job_id == job.id, JobFieldValue.job_document_id.isnot(None))
                    .all())
    per_file: dict[str, dict[str, str]] = {}
    for fv in field_values:
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if val:
            per_file.setdefault(fv.job_document_id, {}).setdefault(fv.label_name, val)

    paired_files = [
        {
            "id": d.id,
            "template_document_id": d.template_document_id,
            "file_index": d.file_index,
            "name": d.original_name or f"{tdoc.name} #{d.file_index + 1}",
            "key": pairing_key(per_file.get(d.id, {})),
        }
        for tdoc in group.documents
        for d in docs_by_tdoc.get(tdoc.id, [])
        if d.file_path
    ]
    doc_sets = assign_sets(paired_files)
    for d in docs:
        d.set_index = doc_sets.get(d.id)
    for fv in field_values:
        fv.set_index = doc_sets.get(fv.job_document_id)
    lines = describe_sets(paired_files, doc_sets)
    db.commit()
    return {"ok": True, "pairing": lines}


@router.post("/jobs/{job_id}/dedupe-field-values")
def dedupe_field_values(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Remove duplicate JobFieldValue rows left behind by the _maybe_auto_extract race that
    used to let two near-simultaneous document uploads both start their own run_extraction
    (fixed with an atomic claim - see that function). Two rows sharing the same (mark_id or
    custom_field_id, set_index, row_index) key is never legitimate - exactly one should exist.

    A re-extraction would also clear this, but it wipes every field and any correction an
    operator already typed on top of the duplicate (the top-of-run_extraction delete removes
    corrected_value along with everything else) - this only removes the genuinely extra rows,
    keeping whichever of a pair actually carries a correction.
    """
    job = _load_job(db, job_id, scope, user)
    rows = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()

    # A row_index-based custom field value has no job_document_id (it isn't tied to one
    # uploaded file), so recompute-doc-sets - which can only look up doc_sets by
    # job_document_id - never re-stamps it. If a job's real documents have SINCE been unified
    # onto one set number by that endpoint, a custom field value still labelled with a set
    # number no real document carries anymore is an orphan of the old numbering, not a
    # genuinely separate line - fold it onto the job's one remaining real set before grouping,
    # so it is still recognised as the duplicate it always was. Only ever acts when there is
    # exactly one real set to fold onto - zero ambiguity about which one that is.
    real_sets = {r.set_index for r in rows if r.job_document_id is not None and r.set_index is not None}
    if len(real_sets) == 1:
        (only_real_set,) = real_sets
        for r in rows:
            if (r.job_document_id is None and r.row_index is not None
                    and r.set_index is not None and r.set_index != only_real_set):
                r.set_index = only_real_set

    groups: dict[tuple, list[JobFieldValue]] = {}
    for r in rows:
        key = (r.mark_id, r.custom_field_id, r.set_index, r.row_index)
        groups.setdefault(key, []).append(r)
    removed = 0
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        # A row actually carrying a correction is never the one discarded; between two
        # otherwise identical rows, the lower id is kept - arbitrary, but deterministic, so
        # calling this twice never does anything the first call didn't already do.
        group.sort(key=lambda r: (0 if (r.corrected_value or "").strip() else 1, r.id))
        for extra in group[1:]:
            db.delete(extra)
            removed += 1
    db.commit()
    return {"ok": True, "removed": removed}
