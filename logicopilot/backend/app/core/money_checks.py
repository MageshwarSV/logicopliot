"""Money checks on a finished job (4-server plan, step P6) - MONEY_CHECKS, off by default.

The invoice's own numbers must add up. Extraction already SETTLES ambiguous numbers where the
arithmetic decides it (extraction.reconcile_row_numbers / reconcile_total - "1.450" read as 1450
because 1450 x 2.83 = 4103.50); these checks VERIFY the finished job and report what still does
not add up, so the operator looks at the paper:

  1. each product line:   quantity x unit price = line amount
  2. the shipment:        sum of every line amount = Product Value
  3. each invoice (set):  sum of its line quantities = its total_quantity

A failed check is a finding on the Cross Docs Verification screen with status "mismatch" - it
blocks "ready" exactly like a cross-document mismatch until someone accepts it. A value is
NEVER changed here. A check whose numbers are missing or not plain numbers is skipped (no
finding): an empty or unreadable value is not evidence of a wrong one.

Same number rules and tolerance as extraction (number_readings, _close: 1% or 0.02), so the two
never disagree about what "adds up" means. The finding id carries a hash of the values shown,
so an acceptance stops applying the moment one of them changes (same as cross-document checks).
"""
from __future__ import annotations

import hashlib
import itertools

QTY, PRICE, AMOUNT = "item_quantity", "item_unit_price", "product_amount"
TOTAL_QTY, PRODUCT_VALUE = "total_quantity", "Product Value"


def enabled() -> bool:
    from app.core.config import get_settings

    return bool(getattr(get_settings(), "money_checks", False))


def _fid(kind: str, *parts) -> str:
    content = hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()[:10]
    return f"money:{kind}:{content}"


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def check_values(lines: dict[tuple, dict[str, str]], totals_qty: dict[int, list[str]],
                 product_values: list[str]) -> list[dict]:
    """The checks themselves, on plain values (no database) - see the module docstring.
    lines: (set, row) -> {label: value}; totals_qty: set -> [total_quantity values];
    product_values: every Product Value on the job."""
    from app.core.extraction import _close, number_readings

    out: list[dict] = []
    amounts: list[float] = []
    qty_by_set: dict[int, list[float]] = {}
    for (s, row), vals in sorted(lines.items()):
        r = {lab: number_readings(vals.get(lab)) for lab in (QTY, PRICE, AMOUNT)}
        if len(r[AMOUNT]) == 1:
            amounts.append(float(r[AMOUNT][0]))
        if len(r[QTY]) == 1:
            qty_by_set.setdefault(s, []).append(float(r[QTY][0]))
        if all(r[lab] for lab in (QTY, PRICE, AMOUNT)):
            fits = [(q, p, a) for q, p, a in itertools.product(r[QTY], r[PRICE], r[AMOUNT])
                    if _close(float(q) * float(p), float(a))]
            if not fits:
                q, p, a = float(r[QTY][0]), float(r[PRICE][0]), float(r[AMOUNT][0])
                out.append({
                    "id": _fid("line", s, row, vals.get(QTY), vals.get(PRICE), vals.get(AMOUNT)),
                    "label": f"Invoice line {row}" + (f" (invoice {s})" if s and s > 1 else "")
                             + ": quantity × unit price = amount",
                    "source_value": f"{vals.get(QTY)} × {vals.get(PRICE)} = {_fmt(q * p)}",
                    "target_value": f"amount {vals.get(AMOUNT)}",
                    "status": "mismatch",
                })
    pv_readings = [float(x) for v in product_values for x in number_readings(v)]
    if amounts and pv_readings and not any(_close(sum(amounts), pv) for pv in pv_readings):
        out.append({
            "id": _fid("total", sum(amounts), *product_values),
            "label": "Sum of invoice line amounts = Product Value",
            "source_value": f"lines add up to {_fmt(sum(amounts))}",
            "target_value": "Product Value " + " / ".join(v for v in product_values if v),
            "status": "mismatch",
        })
    for s, values in sorted(totals_qty.items()):
        line_qty = qty_by_set.get(s)
        readings = [float(x) for v in values for x in number_readings(v)]
        if line_qty and readings and not any(_close(sum(line_qty), t) for t in readings):
            out.append({
                "id": _fid("qty", s, sum(line_qty), *values),
                "label": "Sum of line quantities = total quantity"
                         + (f" (invoice {s})" if s and s > 1 else ""),
                "source_value": f"lines add up to {sum(line_qty):,.3f}".rstrip("0").rstrip("."),
                "target_value": "total_quantity " + " / ".join(v for v in values if v),
                "status": "mismatch",
            })
    return out


def findings(db, job) -> list[dict]:
    """The money findings for one job, each with "accepted". [] when MONEY_CHECKS is off."""
    if not enabled():
        return []
    from app.models.job import JobFieldValue

    lines: dict[tuple, dict[str, str]] = {}
    totals_qty: dict[int, list[str]] = {}
    product_values: list[str] = []
    for fv in db.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.label_name.in_([QTY, PRICE, AMOUNT, TOTAL_QTY, PRODUCT_VALUE])).all():
        value = (fv.value or "").strip()
        if not value:
            continue
        s = fv.set_index or 1
        if fv.row_index is not None and fv.label_name in (QTY, PRICE, AMOUNT):
            lines.setdefault((s, fv.row_index), {})[fv.label_name] = value
        elif fv.row_index is None and fv.label_name == TOTAL_QTY:
            totals_qty.setdefault(s, []).append(value)
        elif fv.row_index is None and fv.label_name == PRODUCT_VALUE:
            product_values.append(value)
    accepted = set(job.accepted_verifications or [])
    out = check_values(lines, totals_qty, product_values)
    for f in out:
        f["accepted"] = f["id"] in accepted
    return out
