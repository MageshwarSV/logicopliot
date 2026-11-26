"""The ERP refused the Excel import - say what to fix, in words an operator can act on.

SOFTLINK answers a bad import with its "Invalid Data Found" table:

    Sheet   Field                Description
    ITEMS   Country_of_Origin    Invoice No. #1 Product No. #1 Entry should be of 2 characters

That used to reach the operator as one long red line of ERP column names, and the operator
phoned support. Here it becomes a short list the operator can fix alone (agreed 2026-11-26):

  - every row is read (code, not AI): sheet, ERP column, invoice no., product no., the rule;
  - each ERP column is traced through the template's ERP Excel mapping to the Cargora box
    that fed it (code, not AI - a field name is never guessed), with the value that was sent
    and the job's own value ids, so the screen can open the box outlined in red;
  - rows that are the same problem are merged: two ERP columns fed by one Cargora box, and
    the same box on several products, are ONE thing to fix;
  - one short AI call writes, per item, what the ERP wants in plain English plus an example.
    Without the AI (no key, timeout) the ERP's own rule is shown instead - never nothing;
  - a column no Cargora box feeds (a fixed value in the Excel setup, a serial number) is
    marked as needing Cargora support, because the operator cannot change it.

build_help() never raises into a run: the caller wraps it, and on any failure the old
erp_reason text is still there.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_HEADING = "invalid data found"
_NOISE = {"sheet field description", "sheet", "field", "description", "close",
          "download error file", "close download error file", "ok", "x", "×"}
_INVOICE = re.compile(r"invoice\s*no\.?\s*#?\s*(\d+)", re.I)
_PRODUCT = re.compile(r"(?:product|item)\s*no\.?\s*#?\s*(\d+)", re.I)
# "ITEMS  Country_of_Origin  Invoice No. #1 ..." when the cells come through without tabs.
_LOOSE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+(\S+)\s+(.+)$")


def parse_rows(text: str) -> list[dict]:
    """Every row of the ERP's "Invalid Data Found" table, in order.

    [{"sheet", "column", "invoice", "product", "rule", "text"}] - invoice/product are ints or
    None (a job-level column names neither). Unreadable lines are skipped, not guessed."""
    if not text:
        return []
    low = text.lower()
    if _HEADING in low:
        text = text[low.index(_HEADING) + len(_HEADING):]
    out: list[dict] = []
    seen: set[tuple] = set()
    # The shortened form ("row | row | row") reads the same once split back into lines.
    for raw in text.replace(" | ", "\n").splitlines():
        line = raw.strip().strip(":").strip()
        if not line or " ".join(line.lower().split()) in _NOISE:
            continue
        cells = [c.strip() for c in re.split(r"\t+|\s{3,}", line) if c.strip()]
        if len(cells) >= 3:
            sheet, column, desc = cells[0], cells[1], " ".join(cells[2:])
        else:
            m = _LOOSE.match(" ".join(line.split()))
            if not m:
                continue
            sheet, column, desc = m.group(1), m.group(2), m.group(3)
        inv = _INVOICE.search(desc)
        prod = _PRODUCT.search(desc)
        rule = _PRODUCT.sub("", _INVOICE.sub("", desc)).strip(" .,:-")
        key = (sheet.lower(), column.lower(), inv and inv.group(1), prod and prod.group(1), rule)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "sheet": sheet, "column": column,
            "invoice": int(inv.group(1)) if inv else None,
            "product": int(prod.group(1)) if prod else None,
            "rule": rule or desc, "text": " ".join(line.split()),
        })
    return out


def _norm(s) -> str:
    return re.sub(r"[\s_\-.]+", "", str(s or "")).lower()


def _row_index(plan: dict, rows: dict, invoice: int | None, product: int | None) -> int:
    """Which data row of the sheet the ERP is talking about (0-based)."""
    from app.core.excel_entry import invoice_of

    scope = plan.get("scope")
    if scope == "invoice":
        return max(0, (invoice or 1) - 1)
    if scope == "job":
        return 0
    if scope == "line":
        depth = max((len(v) for k, v in (rows or {}).items()
                     if isinstance(v, list) and not k.startswith("__")), default=0)
        if invoice:
            mine = [i for i in range(depth) if invoice_of(i, {}, rows) == invoice]
            if product and 0 < product <= len(mine):
                return mine[product - 1]
    return max(0, (product or 1) - 1)


def _edited_cell(job_id: str, sheet: str, header_row: int, column: str, idx: int) -> str | None:
    """The cell the ERP actually read, when the Excel was edited by hand - else None."""
    from app.core.erp_excel_edit import edited_file, read_sheets

    f = edited_file(job_id)
    if f is None:
        return None
    try:
        for s in read_sheets(f):
            if _norm(s["name"]) != _norm(sheet):
                continue
            grid = s["rows"]
            head = grid[header_row - 1] if len(grid) >= header_row else []
            col = next((i for i, h in enumerate(head) if _norm(h) == _norm(column)), None)
            r = header_row + idx
            if col is not None and r < len(grid):
                return grid[r][col] if col < len(grid[r]) else ""
    except Exception:  # noqa: BLE001
        logger.exception("could not read the edited Excel of job %s", job_id)
    return None


def _display_names(db: Session, fvs: list) -> str | None:
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark

    for fv in fvs:
        if fv.mark_id:
            m = db.get(FieldMark, fv.mark_id)
            if m is not None and (m.display_label or "").strip():
                return m.display_label.strip()
        if fv.custom_field_id:
            c = db.get(CustomField, fv.custom_field_id)
            if c is not None and (c.display_label or "").strip():
                return c.display_label.strip()
    return None


def _pretty(label: str) -> str:
    s = re.sub(r"[_]+", " ", label or "").strip()
    return s[:1].upper() + s[1:] if s else s


def _where(places: list[tuple]) -> str:
    """"Invoice 1 · Products 1, 2" / "Invoice 1" / ""."""
    by_inv: dict = {}
    for inv, prod in places:
        by_inv.setdefault(inv, set())
        if prod:
            by_inv[inv].add(prod)
    parts = []
    for inv in sorted(by_inv, key=lambda x: (x is None, x or 0)):
        prods = sorted(by_inv[inv])
        p = (f"Product {prods[0]}" if len(prods) == 1
             else f"Products {', '.join(map(str, prods))}") if prods else ""
        parts.append(" · ".join(filter(None, [f"Invoice {inv}" if inv else "", p])))
    return "; ".join(filter(None, parts))


def build_items(db: Session, job, erp_rows: list[dict]) -> list[dict]:
    """The ERP rows, traced to Cargora boxes and merged into things to fix. No AI."""
    from app.core.excel_entry import SERIALS, _literals, _picked_field, _rows_for_plan, sheet_plans
    from app.models.job import JobFieldValue
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id) if job.group_id else None
    cfg = (group.excel_config or {}) if group is not None else {}
    plans = sheet_plans(cfg)
    values: dict = {}
    rows: dict = {}
    if plans:
        from app.api.v1.jobs import entry_values_and_rows

        values, rows = entry_values_and_rows(db, job)
    tables: dict[str, list] = {}
    fvs_by_label: dict[str, list] = {}
    for fv in db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all():
        fvs_by_label.setdefault(fv.label_name, []).append(fv)

    items: dict[tuple, dict] = {}
    for r in erp_rows:
        plan = next((p for p in plans if _norm(p.get("sheet")) == _norm(r["sheet"])), None)
        col = None
        if plan is not None:
            col = next((c for c in plan["columns"]
                        if _norm(c.get("header") or c.get("field")) == _norm(r["column"])), None)
        label = ""
        kind = "unmapped"
        sent = None
        if col is not None:
            label = (_picked_field(col, values) or "").split("@@")[0].strip()
            if label in SERIALS:
                kind = "serial"
            elif label:
                kind = "field"
            elif _literals(col) is not None:
                kind = "fixed"
            idx = _row_index(plan, rows, r["invoice"], r["product"])
            edited = _edited_cell(job.id, plan.get("sheet") or r["sheet"], plan["header_row"], r["column"], idx)
            if edited is not None:
                sent = edited
            else:
                key = plan.get("sheet") or ""
                if key not in tables:
                    try:
                        tables[key] = _rows_for_plan(plan, values, rows)
                    except Exception:  # noqa: BLE001
                        tables[key] = []
                data = tables[key]
                ci = plan["columns"].index(col)
                if idx < len(data) and ci < len(data[idx]):
                    sent = data[idx][ci]
        ids: list[str] = []
        fvs = fvs_by_label.get(label, []) if kind == "field" else []
        if kind == "field" and fvs:
            per_row = [f for f in fvs if f.row_index is not None]
            if per_row and r["product"]:
                sets = sorted({f.set_index or 1 for f in per_row})
                s = sets[(r["invoice"] or 1) - 1] if (r["invoice"] or 1) <= len(sets) else sets[0]
                lines = sorted({f.row_index for f in per_row if (f.set_index or 1) == s})
                row = lines[r["product"] - 1] if r["product"] <= len(lines) else None
                ids = [f.id for f in per_row if (f.set_index or 1) == s and f.row_index == row]
            else:
                ids = [f.id for f in fvs if f.row_index is None] or [f.id for f in fvs]
        gkey = ("field", label) if kind == "field" else ("col", _norm(r["sheet"]), _norm(r["column"]))
        it = items.get(gkey)
        if it is None:
            name = (_display_names(db, fvs) if fvs else None) or _pretty(label or r["column"])
            fixable = kind == "field" and bool(fvs)
            support = None
            if not fixable:
                support = {
                    "fixed": "This value is set in the ERP Excel setup, not on the job.",
                    "serial": "This number is made by Cargora, not typed on the job.",
                    "field": "There is no box for this on the job.",
                }.get(kind, "Cargora could not find which box fills this ERP column.")
            it = items[gkey] = {
                "field": label or None, "display": name, "erp_columns": [], "sheet": r["sheet"],
                "places": [], "sent": [], "rules": [], "fixable": fixable,
                "support_reason": support, "field_value_ids": [],
            }
        if r["column"] not in it["erp_columns"]:
            it["erp_columns"].append(r["column"])
        place = (r["invoice"], r["product"])
        if place not in [tuple(p) for p in it["places"]]:
            it["places"].append(list(place))
        sv = "" if sent is None else str(sent)
        if sent is not None and sv not in it["sent"]:
            it["sent"].append(sv)
        if r["rule"] not in it["rules"]:
            it["rules"].append(r["rule"])
        for i in ids:
            if i not in it["field_value_ids"]:
                it["field_value_ids"].append(i)
    out = list(items.values())
    for it in out:
        it["where"] = _where([tuple(p) for p in it["places"]])
        it["needs"] = "; ".join(it["rules"])
        it["example"] = ""
        it["suggest"] = ""
    return out


_EXPLAIN_SYSTEM = (
    "You help logistics operators in Tamil Nadu, India, fix a customs job that the ERP refused. "
    "They are not technical. Write simple English: short sentences, everyday words, no ERP "
    "column names, no technical words like 'characters', 'field', 'string' or 'format' - say "
    "'letters', 'digits', 'box' instead.\n"
    "You get a numbered list of problems. Each has the Cargora box name, the value Cargora sent, "
    "and the ERP's own rule(s).\n"
    "For each problem write:\n"
    "- needs: ONE short sentence saying what the ERP wants in that box. Do not repeat the box "
    "name. Example: 'A 2-letter country code.'\n"
    "- example: one or two short examples of a correct value, e.g. 'CN for China, US for USA'. "
    "Empty if no example makes sense.\n"
    "- suggest: the corrected value for the value that was sent, ONLY when you are certain "
    "(e.g. sent 'China', rule is 2 letters -> 'CN'). Otherwise empty. Never guess a number, "
    "date, code or name you cannot derive from the sent value.\n"
    "Only use what is in the input. Never invent a rule. If a rule is unclear, restate the "
    "ERP's words simply.\n"
    'Reply JSON: {"items": [{"n": 1, "needs": "...", "example": "...", "suggest": "..."}]}'
)


def explain(items: list[dict], tenant_id: str | None) -> bool:
    """Fill each item's needs / example / suggest from one AI call. True when the AI answered."""
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    if not items:
        return False
    settings = get_settings()
    if not settings.openai_api_key:
        return False
    lines = []
    for n, it in enumerate(items, start=1):
        sent = ", ".join(f'"{s}"' for s in it["sent"][:5]) or "(empty)"
        lines.append(f"Problem {n}: box '{it['display']}'; value sent: {sent}; "
                     f"ERP rule: {' / '.join(it['rules'][:3])}")
    try:
        resp = create_chat_completion_with_retry(
            timeout=30, tenant_id=tenant_id, model=settings.openai_model, temperature=0,
            max_tokens=80 + 70 * len(items), response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _EXPLAIN_SYSTEM},
                      {"role": "user", "content": "\n".join(lines)}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 - the ERP's own rule is shown instead
        logger.warning("ERP rejection explain call failed: %s", exc)
        return False
    got = False
    for row in data.get("items") or []:
        try:
            it = items[int(row.get("n")) - 1]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        needs = str(row.get("needs") or "").strip()
        if needs:
            it["needs"] = needs[:200]
            got = True
        it["example"] = str(row.get("example") or "").strip()[:120]
        sug = str(row.get("suggest") or "").strip()[:60]
        # A suggestion is only offered for a single sent value it was derived from.
        it["suggest"] = sug if sug and len(set(it["sent"])) == 1 else ""
    return got


def build_help(db: Session, job, raw_text: str, *, use_ai: bool = True) -> dict | None:
    """What the job screen shows for an ERP import rejection, or None when nothing could be
    read from the ERP's table (the old message then stays the only one)."""
    from app.core.erp_excel_edit import edited_file

    erp_rows = parse_rows(raw_text)
    if not erp_rows:
        return None
    items = build_items(db, job, erp_rows)
    ai = explain(items, job.tenant_id) if use_ai else False
    return {
        "kind": "import_rejected",
        "items": items,
        "erp_rows": erp_rows,
        "excel_edited": edited_file(job.id) is not None,
        "ai": ai,
    }
