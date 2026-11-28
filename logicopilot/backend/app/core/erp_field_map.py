"""ERP Field Configuration (R7 + R2, 2026-11-28) - one row per ERP Excel column of a template:
which document and field feed it, whether it is mandatory, the rules that check it, where it
lands in the ERP workbook, and whether that mapping actually works.

    Document | Document field | Mandatory | Rules | Excel file | Sheet | Column | ERP field | Status

"Mandatory" has ONE meaning here and in the pre-ERP check: the column's own rule
{"required": true} (TemplateGroup.erp_column_rules). The other flags that used to mean
"mandatory" are only SUGGESTIONS for it, each with its reason:

  - the field is "Ask operator + required" on the job screen
  - the ERP refused this column as empty (failure reports / ERP rejections)
  - the column had a value on (almost) every job the ERP accepted  (analyse(), on demand)
  - the onboarding ERP Field list marks it mandatory

Mapping status:
  configured  a source that exists (a document / job field, or a fixed value)
  missing     a MANDATORY column with no source - it always goes out empty
  invalid     the source does not work: the field is not on the template, from_document
              names a document the template does not have, a rule pattern does not compile,
              an unknown transform, or text is cut at a length limit
  not_used    no source, not mandatory (most ERP columns - left empty on purpose)
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.erp_rules import _columns, describe, effective_rules, rule_key

ANALYSIS_KEY = "_analysis"
FILL_SUGGEST_PCT = 98          # filled on >= this % of accepted jobs -> suggest mandatory
FILL_MIN_JOBS = 10             # ... measured on at least this many jobs
REPORT_WINDOW_DAYS = 90
ACCEPTED_MAX = 20              # a column with at most this many distinct accepted values gets
                               # them offered as its "allowed values" list


def _base(label: str | None) -> str:
    return str(label or "").split("@@")[0].strip()


def field_index(db: Session, group) -> dict[str, dict]:
    """{label: what the template knows about that field}."""
    from app.models.custom_field import CustomField

    out: dict[str, dict] = {}
    for doc in getattr(group, "documents", None) or []:
        for m in doc.marks or []:
            e = out.setdefault(m.label_name, {"documents": [], "doc_types": [], "kind": "document",
                                              "ask_required": False, "patterns": [], "options": 0,
                                              "per_line": False, "display": m.display_label or None})
            if doc.name not in e["documents"]:
                e["documents"].append(doc.name)
                e["doc_types"].append(doc.doc_type)
            e["ask_required"] |= bool(m.ask_operator and m.ask_operator_required)
            if (m.validation_pattern or "").strip():
                e["patterns"].append(m.validation_message or f"format {m.validation_pattern}")
            e["options"] = max(e["options"], len(m.fixed_options or []))
            e["per_line"] |= bool(m.is_multi_value or m.standalone_multi_value)
    docs_by_id = {d.id: d.name for d in getattr(group, "documents", None) or []}
    for c in db.query(CustomField).filter(CustomField.group_id == group.id).all():
        e = out.get(c.label_name)
        if e is None:
            src = [docs_by_id[i] for i in (c.source_document_ids or []) if i in docs_by_id]
            kind = ("job field (operator picks)" if c.ask_operator
                    else {"hardcoded": "job field (default value)", "lookup": "lookup (master data)",
                          "composite": "combined field"}.get(c.kind or "", "job field (AI)"))
            e = out[c.label_name] = {"documents": src, "doc_types": [], "kind": kind, "ask_required": False,
                                     "patterns": [], "options": 0, "per_line": bool(c.per_row),
                                     "display": c.display_label or None}
        e["ask_required"] |= bool(c.ask_operator and c.ask_operator_required)
        if (c.validation_pattern or "").strip():
            e["patterns"].append(c.validation_message or f"format {c.validation_pattern}")
        e["options"] = max(e["options"], len(c.fixed_options or []))
    return out


def _labels_of(col: dict) -> list[str]:
    labels = [_base(col.get("field"))] + [_base(x) for x in (col.get("fallback_fields") or [])]
    pick = col.get("pick_by") if isinstance(col.get("pick_by"), dict) else None
    if pick:
        labels += [_base(v) for v in (pick.get("options") or {}).values()]
    seen, out = set(), []
    for lb in labels:
        if lb and not lb.startswith("#") and lb not in seen:
            seen.add(lb)
            out.append(lb)
    return out


def _placeholders(col: dict) -> list[str]:
    vals = [col.get("value")] + list(col.get("values") or [])
    return [m.strip() for v in vals if isinstance(v, str) for m in re.findall(r"\[\[([^\]]+)\]\]", v)]


def refused_counts(db: Session, group) -> dict[tuple[str, str], dict]:
    """{(sheet, header): {"count", "empty", "last"}} from the ERP's own refusals on this
    template's jobs (failure reports + the rejection card), last REPORT_WINDOW_DAYS days."""
    from app.models.job import Job
    from app.models.job_failure import JobFailureReport

    since = datetime.now(timezone.utc) - timedelta(days=REPORT_WINDOW_DAYS)
    heads = {(p.get("sheet") or "", h) for p, _c, h in _columns(group.excel_config or {})}
    pats = {k: re.compile(rf"\b{re.escape(k[0])}\s+{re.escape(k[1])}\b", re.I) for k in heads if k[0] and k[1]}
    out: dict[tuple[str, str], dict] = {}
    texts: list[tuple[str, str]] = []
    rows = (db.query(JobFailureReport.reason, JobFailureReport.job_reference)
            .join(Job, Job.id == JobFailureReport.job_id)
            .filter(Job.group_id == group.id, JobFailureReport.created_at >= since).all())
    texts += [(r[0] or "", r[1] or "") for r in rows]
    for j in db.query(Job).filter(Job.group_id == group.id, Job.erp_failed_at >= since).all():
        for r in ((j.erp_help or {}).get("erp_rows") or []) if isinstance(j.erp_help, dict) else []:
            texts.append((f"{r.get('sheet')} {r.get('column')} {r.get('rule')}", j.reference))
    for text, ref in texts:
        for k, pat in pats.items():
            m = pat.search(text)
            if not m:
                continue
            e = out.setdefault(k, {"count": 0, "empty": 0, "last": ref})
            e["count"] += 1
            e["last"] = ref
            tail = text[m.end():m.end() + 160].lower()
            if any(w in tail for w in ("mandatory", "required", "blank", "empty", "not entered")):
                e["empty"] += 1
    return out


def config_rows(db: Session, group) -> dict:
    from app.core.excel_entry import _RULE_TRANSFORMS, _TRANSFORMS

    cfg = group.excel_config or {}
    stored = group.erp_column_rules or {}
    active = effective_rules(group)
    idx = field_index(db, group)
    doc_names = {d.name for d in getattr(group, "documents", None) or []}
    refused = refused_counts(db, group)
    analysis = stored.get(ANALYSIS_KEY) or {}
    fill = analysis.get("fill") or {}
    erp_fields = _erp_field_labels(db, group)
    file_name = cfg.get("file_name") or ""

    rows: list[dict] = []
    for plan, col, header in _columns(cfg):
        sheet = plan.get("sheet") or ""
        key = rule_key(sheet, header)
        rule = active.get(key) or {}
        labels = _labels_of(col)
        ph = _placeholders(col)
        fixed = col.get("value") if col.get("value") not in (None, "") else None
        problems: list[str] = []
        known = [lb for lb in labels if lb in idx]
        for lb in labels:
            if lb not in idx:
                problems.append(f"Field '{lb}' is not on this template - the column always goes out empty")
        for lb in ph:
            if lb not in idx:
                problems.append(f"[[{lb}]] in the fixed text is not a field on this template")
        fd = str(col.get("from_document") or "").strip()
        if fd and fd not in doc_names:
            problems.append(f"Reads only from document '{fd}', which this template does not have")
        tr = col.get("transform")
        if tr and tr not in _TRANSFORMS and tr not in _RULE_TRANSFORMS:
            problems.append(f"Unknown conversion '{tr}'")
        if rule.get("pattern"):
            try:
                re.compile(rule["pattern"])
            except re.error:
                problems.append("The rule's pattern does not work (it is skipped)")
        if col.get("max_len"):
            problems.append(f"Longer text is cut to {col['max_len']} characters without warning")

        has_source = bool(labels or fixed is not None or col.get("values") or ph)
        mandatory = bool(rule.get("required"))
        # Invalid = a source is set but nothing in it works (the column always goes out empty).
        # A chain where one field still works stays configured, its problem listed beside it.
        working = bool(known or fixed is not None or col.get("values") or any(lb in idx for lb in ph))
        if has_source and not working:
            status = "invalid"
        elif not has_source:
            status = "missing" if mandatory else "not_used"
        else:
            status = "configured"

        src = idx.get(known[0]) if known else None
        if fixed is not None and not labels:
            document, doc_field = "Fixed value", str(fixed)
        elif src:
            document = ", ".join(src["documents"]) or src["kind"].capitalize()
            if fd:
                document = fd
            doc_field = known[0] + (f" (then {', '.join(known[1:])})" if len(known) > 1 else "")
        elif labels:
            document, doc_field = "—", labels[0]
        else:
            document, doc_field = "", ""

        # Strong reasons say the ERP needs it; the fill rate only says it was always there.
        reasons: list[str] = []
        if src and src.get("ask_required"):
            reasons.append("Ask operator + required on the job screen")
        rf = refused.get((sheet, header))
        if rf and rf["empty"]:
            reasons.append(f"ERP refused it as empty {rf['empty']}×")
        if erp_fields.get((sheet, header), {}).get("mandatory"):
            reasons.append("Marked mandatory in the onboarding ERP field list")
        strong = bool(reasons)
        pct = fill.get(key)
        can_be_empty = bool(known) and not (fixed is not None and not labels)
        if (can_be_empty and pct is not None and pct >= FILL_SUGGEST_PCT
                and (analysis.get("jobs") or 0) >= FILL_MIN_JOBS):
            reasons.append(f"Had a value on {pct}% of jobs the ERP accepted")

        rule_texts = []
        if key in active:
            rule_texts.append(("Built in: " if active[key].get("builtin") else "") + describe(active[key]))
        if src:
            rule_texts += [f"Job screen: {p}" for p in src["patterns"]]
            if src["options"]:
                rule_texts.append(f"Job screen: pick from {src['options']} options")
        if tr:
            rule_texts.append(f"Converted: {tr}")

        rows.append({
            "key": key, "sheet": sheet, "scope": plan.get("scope"), "column": col.get("column") or "",
            "header": header, "erp_field": erp_fields.get((sheet, header), {}).get("label") or header,
            "document": document, "doc_field": doc_field, "per_line": plan.get("scope") == "line",
            "fixed_value": fixed, "mandatory": mandatory,
            "suggest_mandatory": (not mandatory) and bool(reasons) and has_source,
            "suggest_strength": ("strong" if strong else "data") if reasons else None,
            "mandatory_reasons": reasons, "rules": rule_texts, "rule": stored.get(key),
            "builtin": bool(active.get(key, {}).get("builtin")),
            "status": status, "problems": problems, "excel_file": file_name,
            "refused": rf["count"] if rf else 0, "refused_last": rf["last"] if rf else None,
            "fill_pct": pct,
            "accepted_values": (analysis.get("values") or {}).get(key) if not (fixed is not None and not labels) else None,
        })
    summary = Counter(r["status"] for r in rows)
    return {
        "excel_file": file_name, "rows": rows,
        "summary": {"total": len(rows), "configured": summary["configured"], "missing": summary["missing"],
                    "invalid": summary["invalid"], "not_used": summary["not_used"],
                    "mandatory": sum(1 for r in rows if r["mandatory"]),
                    "suggested": sum(1 for r in rows if r["suggest_mandatory"]),
                    "suggested_strong": sum(1 for r in rows if r["suggest_mandatory"] and r["suggest_strength"] == "strong"),
                    "refused": sum(1 for r in rows if r["refused"])},
        "analysis": {"at": analysis.get("at"), "jobs": analysis.get("jobs") or 0},
    }


def _erp_field_labels(db: Session, group) -> dict[tuple[str, str], dict]:
    from app.models.erp_mapping import ErpField

    out = {}
    for f in db.query(ErpField).filter(ErpField.group_id == group.id).all():
        out[(f.sheet_name or "", f.excel_name or "")] = {"label": f.display_label or f.excel_name,
                                                         "mandatory": bool(f.mandatory)}
    return out


def analyse(db: Session, group, limit: int = 80) -> dict:
    """How often each column had a value on recent jobs the ERP ACCEPTED - a column filled on
    nearly every one of them is very likely one the ERP needs. Stored on the template (so the
    page does not rebuild 80 workbooks on every visit); never changes a rule by itself."""
    from app.api.v1.jobs import entry_values_and_rows
    from app.core.erp_rules import effective_config
    from app.core.excel_entry import _rows_for_plan, sheet_plans
    from app.models.job import Job

    from sqlalchemy import or_

    jobs = (db.query(Job).filter(Job.group_id == group.id, Job.ai_submitted_at.isnot(None),
                                 Job.status != "failed",
                                 or_(Job.gk2_status.is_(None), Job.gk2_status != "failed"))
            .order_by(Job.ai_submitted_at.desc()).limit(limit).all())
    cfg = effective_config(group)
    filled: Counter = Counter()
    seen: Counter = Counter()
    distinct: dict[str, set] = {}
    used = 0
    for j in jobs:
        try:
            values, rows = entry_values_and_rows(db, j)
        except Exception:  # noqa: BLE001 - one odd job does not stop the analysis
            continue
        used += 1
        for plan in sheet_plans(cfg):
            try:
                data = _rows_for_plan(plan, values, rows)
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue
            for ci, c in enumerate(plan["columns"]):
                key = rule_key(plan.get("sheet"), c.get("header") or c.get("field") or "")
                seen[key] += 1
                cells = [str((ln[ci] if ci < len(ln) else "") or "").strip() for ln in data]
                if all(cells):
                    filled[key] += 1
                bucket = distinct.setdefault(key, set())
                if len(bucket) <= ACCEPTED_MAX:
                    bucket.update(c.upper() for c in cells if c)
    fill = {k: round(100 * filled[k] / n) for k, n in seen.items() if n}
    stored = dict(group.erp_column_rules or {})
    accepted = {k: sorted(v) for k, v in distinct.items() if 0 < len(v) <= ACCEPTED_MAX}
    stored[ANALYSIS_KEY] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "jobs": used,
                            "fill": fill, "values": accepted}
    group.erp_column_rules = stored
    db.commit()
    return {"jobs": used, "columns": len(fill)}
