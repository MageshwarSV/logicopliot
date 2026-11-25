"""Auto ERP Submission: no mandatory ERP column goes out empty.

Found on production (JOB-E739B0, 2026-11-25): the product-line "unit" (a document field with a
list of 70 unit codes) was empty on all 16 lines, so LogiLENS refused the import - "ITEMS / Unit
/ This field is mandatory" for every product. With Auto ERP Submission on nothing in Cargora may
stop the job (business rule), so right before the ERP workbook is built the values are
completed, for auto-submitted jobs only:

  A column whose field has a list of choices (header, every product line, invoice rows):
    1. a valid value stays;
    2. a value not in the list -> the nearest choice (PCS / PC / PIECES / NOS, KG / KGS, ...);
    3. empty on a product line -> the value the other lines of this job use, else the job's
       quantity unit;
    4. the value this template's completed jobs use most for that field;
    5. the first choice.
  A column whose field has no list, but which Cargora marks required (ask the operator,
  required): "NA" for text, 0 for numbers.

Nothing is written to the job's own values - the screen still shows what was read. Every
value filled is returned so the job note can list it for 4S to correct in the ERP. Manual
jobs never come here.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

from sqlalchemy.orm import Session

_UNIT_GROUPS = [
    {"PCS", "PC", "PCE", "PIECE", "PIECES", "NOS", "NO", "NUMBER", "NUMBERS", "EA", "EACH", "UNIT",
     "UNITS", "UNT", "U"},
    {"KGS", "KG", "KILOGRAM", "KILOGRAMS", "KGM", "KILO"},
    {"GMS", "G", "GM", "GRAM", "GRAMS", "GRM"},
    {"MTR", "M", "METER", "METERS", "METRE", "METRES", "MTRS"},
    {"SET", "SETS", "SETS."},
    {"PRS", "PAIR", "PAIRS", "PR"},
    {"LTR", "L", "LITRE", "LITRES", "LITER", "LITERS", "LTRS"},
    {"BOX", "BOXES", "BXS"},
    {"ROL", "ROLL", "ROLLS"},
    {"TON", "TONS", "TONNE", "TONNES", "MT", "MTS"},
]
_NUMBER_HINTS = ("qty", "quantity", "value", "amount", "amt", "weight", "wt", "price", "rate",
                 "no_of", "no of", "count", "total", "fob", "cif", "freight", "insurance", "duty")
_QTY_UNIT_LABELS = ("total quantity unit", "quantity unit", "qty unit", "total qty unit")


def _code(opt) -> str:
    v = opt.get("value") or opt.get("label") if isinstance(opt, dict) else opt
    v = str(v or "").strip()
    return v.split(":", 1)[0].strip() if ":" in v else v


def _norm(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def nearest_option(value: str, options: list[str]) -> str | None:
    """The list's own code for a value written another way, or None."""
    n = _norm(value)
    if not n:
        return None
    by_norm = {_norm(o): o for o in options}
    if n in by_norm:
        return by_norm[n]
    for group in _UNIT_GROUPS:
        if n in group:
            same = [o for o in options if _norm(o) in group]
            if same:
                # PIECES -> PCS rather than NOS: the closest spelling within the group.
                return max(same, key=lambda o: difflib.SequenceMatcher(None, n, _norm(o)).ratio())
    close = difflib.get_close_matches(n, list(by_norm), n=1, cutoff=0.85)
    return by_norm[close[0]] if close else None


def _empty(v) -> bool:
    return not str(v if v is not None else "").strip()


def _numeric(label: str, past: list[str]) -> bool:
    nums = [p for p in past if re.fullmatch(r"-?[\d,]*\.?\d+", p.strip())]
    if past:
        return len(nums) >= max(1, len(past) // 2)
    lo = label.lower()
    return any(h in lo for h in _NUMBER_HINTS)


class _Fields:
    """What the template knows about each field label: its choices and whether it is required."""

    def __init__(self, db: Session, job):
        from app.models.custom_field import CustomField
        from app.models.field_mark import FieldMark
        from app.models.template_document import TemplateDocument

        self.db, self.job = db, job
        self.options: dict[str, list[str]] = {}
        self.required: set[str] = set()
        marks = (db.query(FieldMark).join(TemplateDocument, TemplateDocument.id == FieldMark.document_id)
                 .filter(TemplateDocument.group_id == job.group_id).all())
        cfs = db.query(CustomField).filter(CustomField.group_id == job.group_id).all()
        for f in [*marks, *cfs]:
            label = (f.label_name or "").strip()
            opts = [c for c in (_code(o) for o in (f.fixed_options or [])) if c]
            if opts and label not in self.options:
                self.options[label] = opts
            if getattr(f, "ask_operator", False) and getattr(f, "ask_operator_required", False):
                self.required.add(label)
        self._past: dict[str, list[str]] = {}

    def past(self, label: str) -> list[str]:
        """This field's values on the template's latest completed jobs, newest first."""
        if label not in self._past:
            from app.models.job import Job, JobFieldValue

            recent = [r[0] for r in self.db.query(Job.id).filter(
                Job.group_id == self.job.group_id, Job.status == "completed", Job.id != self.job.id)
                .order_by(Job.updated_at.desc()).limit(200).all()]
            vals = []
            if recent:
                for fv in self.db.query(JobFieldValue).filter(JobFieldValue.job_id.in_(recent),
                                                               JobFieldValue.label_name == label).all():
                    v = (fv.value or "").strip()
                    if v:
                        vals.append(v)
            self._past[label] = vals
        return self._past[label]

    def most_used(self, label: str, options: list[str] | None) -> str | None:
        counts = Counter(self.past(label))
        for v, _n in counts.most_common():
            if not options:
                return v
            hit = nearest_option(v, options)
            if hit:
                return hit
        return None


def complete_for_erp(db: Session, job, config: dict, values: dict, rows: dict) -> tuple[dict, dict, list[str]]:
    """(values, rows, notes) with every mandatory ERP column filled - see the module docstring."""
    from app.core.excel_entry import SERIALS, _literals, _picked_field, _set_values, sheet_plans

    values = dict(values or {})
    rows = {k: (list(v) if isinstance(v, list) else v) for k, v in (rows or {}).items()}
    info = _Fields(db, job)
    filled: dict[str, list] = {}           # label -> [(where, value, how)]

    def note(label, where, value, how):
        filled.setdefault(label, []).append((where, value, how))

    def qty_unit(options):
        for k, v in values.items():
            if k.split("@@")[0].strip().lower() in _QTY_UNIT_LABELS and not _empty(v):
                hit = nearest_option(str(v), options)
                if hit:
                    return hit
        return None

    def choose(label: str, current, siblings: list) -> tuple[str | None, str]:
        base = label.split("@@")[0].strip()
        options = info.options.get(base)
        if options:
            if not _empty(current):
                if str(current).strip() in options:
                    return None, ""                         # valid: keep
                hit = nearest_option(str(current), options)
                if hit:
                    return hit, f'nearest choice for "{current}"'
            sib = Counter(nearest_option(str(s), options) for s in siblings if not _empty(s))
            sib.pop(None, None)
            if sib:
                return sib.most_common(1)[0][0], "same as the other lines"
            if "unit" in base.lower():
                hit = qty_unit(options)
                if hit:
                    return hit, "the job's quantity unit"
            hit = info.most_used(base, options)
            if hit:
                return hit, "most used on this template's jobs"
            return options[0], "first choice"
        if base in info.required and _empty(current):
            return ("0", "placeholder") if _numeric(base, info.past(base)) else ("NA", "placeholder")
        return None, ""

    for plan in sheet_plans(config or {}):
        cols = plan["columns"]
        labels = [_picked_field(c, values) for c in cols]
        depth = 0
        if plan["scope"] == "line":
            for lbl in labels:
                if lbl and lbl not in SERIALS and isinstance(rows.get(lbl), list):
                    depth = max(depth, len(rows[lbl]))
        for col, label in zip(cols, labels):
            if not label or label in SERIALS or _literals(col) is not None:
                continue
            if col.get("fallback_fields"):
                fb_vals = [values.get(f) for f in col["fallback_fields"]]
                if any(not _empty(v) for v in fb_vals):
                    continue
            if plan["scope"] == "line" and not depth:
                continue                      # no product lines on this job: nothing to fill
            if plan["scope"] == "line":
                seq = rows.get(label)
                if not isinstance(seq, list):
                    seq = [values.get(label, "")] * depth if not _empty(values.get(label)) else [""] * depth
                seq = list(seq) + [""] * (depth - len(seq))
                for i in range(depth):
                    new, how = choose(label, seq[i], seq)
                    if new is not None:
                        seq[i] = new
                        note(label, f"line {i + 1}", new, how)
                rows[label] = seq
            elif plan["scope"] == "invoice" and _set_values(rows):
                for n, d in enumerate(_set_values(rows), 1):
                    cur = d.get(label, values.get(label, ""))
                    new, how = choose(label, cur, [])
                    if new is not None:
                        d[label] = new
                        note(label, f"invoice {n}", new, how)
            else:
                new, how = choose(label, values.get(label, ""), [])
                if new is not None:
                    values[label] = new
                    note(label, "", new, how)

    notes = []
    for label, items in filled.items():
        name = label.split("@@")[0]
        vals = Counter((v, how) for _w, v, how in items)
        parts = []
        lines = items[0][0].startswith("line")
        for (v, how), n in vals.items():
            where = f" ({n} line{'s' if n > 1 else ''})" if lines else (f" ({n} invoices)" if n > 1 else "")
            parts.append(f"{v}{where}, {how}")
        notes.append(f"{name} = " + "; ".join(parts))
    return values, rows, notes


def fill_job_values(db: Session, job) -> list[str]:
    """Save a value into every field of the job that has a list of choices and is empty (or
    holds something not in its list) - header fields and every product line - so the job screen
    shows what goes to the ERP instead of "not found". Same choice rules as complete_for_erp.
    Written as the extracted value, never as a correction; a person's correction is kept as it
    is. Required fields with no list get NA / 0. Returns "Label = value (how)" lines."""
    from app.models.job import JobFieldValue

    info = _Fields(db, job)
    fvs = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    header = {fv.label_name: fv for fv in fvs if fv.row_index is None}
    lines: dict[tuple, list] = {}
    for fv in fvs:
        if fv.row_index is not None:
            lines.setdefault((fv.label_name, fv.set_index), []).append(fv)
    filled: dict[str, list] = {}

    def valid(label, v) -> bool:
        opts = info.options.get(label)
        return not _empty(v) and (not opts or str(v).strip() in opts)

    def set_value(fv, value, how, where):
        fv.extracted_value = value
        filled.setdefault(fv.label_name, []).append((where, value, how))

    def header_qty_unit(options):
        for label, fv in header.items():
            if label.strip().lower() in _QTY_UNIT_LABELS and valid(label, fv.value):
                return nearest_option(fv.value, options)
        return None

    def from_lines(options):
        c = Counter()
        for (label, _s), group in lines.items():
            lo = label.lower()
            if "unit" in lo or "quantity type" in lo:
                for fv in group:
                    hit = nearest_option(fv.value or "", options) if not _empty(fv.value) else None
                    if hit:
                        c[hit] += 1
        return c.most_common(1)[0][0] if c else None

    def pick(label, current, siblings, *, line: bool):
        options = info.options[label]
        if not _empty(current):
            hit = nearest_option(str(current), options)
            if hit:
                return hit, f'nearest choice for "{current}"'
        sib = Counter(nearest_option(str(s), options) for s in siblings if not _empty(s))
        sib.pop(None, None)
        if sib:
            return sib.most_common(1)[0][0], "same as the other lines"
        lo = label.lower()
        if line and ("unit" in lo or "quantity type" in lo):
            hit = header_qty_unit(options)
            if hit:
                return hit, "the job's quantity unit"
        if not line and "unit" in lo and "quantity" in lo:
            hit = from_lines(options)
            if hit:
                return hit, "the product lines' unit"
        hit = info.most_used(label, options)
        if hit:
            return hit, "most used on this template's jobs"
        return options[0], "first choice"

    # Product lines first, so an empty header quantity unit can take the lines' unit.
    for (label, set_index), group in lines.items():
        if label not in info.options:
            continue
        for fv in sorted(group, key=lambda f: f.row_index):
            if fv.corrected_value is not None or valid(label, fv.value):
                continue
            value, how = pick(label, fv.value, [g.value for g in group if valid(label, g.value)], line=True)
            set_value(fv, value, how, f"line {fv.row_index}")
    for label, fv in header.items():
        if fv.corrected_value is not None or valid(label, fv.value):
            continue
        if label in info.options:
            value, how = pick(label, fv.value, [], line=False)
            set_value(fv, value, how, "")
        elif label in info.required and _empty(fv.value):
            value = "0" if _numeric(label, info.past(label)) else "NA"
            set_value(fv, value, "placeholder", "")
    db.flush()

    notes = []
    for label, items in filled.items():
        vals = Counter((v, how) for _w, v, how in items)
        is_line = items[0][0].startswith("line")
        parts = [f"{v}" + (f" ({n} line{'s' if n > 1 else ''})" if is_line else "") + f", {how}"
                 for (v, how), n in vals.items()]
        notes.append(f"{label} = " + "; ".join(parts))
    return notes
