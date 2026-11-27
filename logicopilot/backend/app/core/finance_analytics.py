"""Investor-ready business analytics for SaaS Finance (2026-11-27).

Revenue is NET of GST (what Axiora earns). Rules, so every figure matches what was billed:
  - a finished month comes from its usage invoice (platform fee + page usage) plus the manual
    invoices issued in it (cancelled and draft ones excluded);
  - the current month is live (summarize_tenant_bill - the same figure the dashboard shows) plus
    manual invoices issued so far;
  - the daily view spreads each customer's page-usage revenue for the month over its days by
    billable pages (BillablePageEvent), so the days add up to the month; the platform fee is a
    monthly charge and is shown as such, never spread.
"Product" has two views: shipment type (Air / Sea, Import / Export - page usage split by the
customer's pages that month) and revenue type (platform fee / page usage / other services =
manual invoices).
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

IST = timezone(timedelta(hours=5, minutes=30))
SHIPMENT_TYPES = ["Air Import", "Air Export", "Sea Import", "Sea Export"]
REVENUE_TYPES = ["Platform fee", "Page usage", "Other services"]


def today_ist() -> date:
    return datetime.now(IST).date()


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _norm_type(raw: str | None) -> str:
    s = (raw or "").lower()
    air, sea = "air" in s, "sea" in s
    imp, exp = "import" in s, "export" in s
    if air and exp:
        return "Air Export"
    if air:
        return "Air Import"
    if sea and exp:
        return "Sea Export"
    if sea:
        return "Sea Import"
    return "Other"


class Ledger:
    """Per-request cache of every month's figures per customer."""

    def __init__(self, db: Session):
        from app.models.tenant import Tenant

        self.db = db
        self.tenants = {t.id: t for t in db.query(Tenant).all()}
        self._month: dict[date, dict] = {}
        self._events: dict[date, list] | None = None
        self.today = today_ist()
        self.current = month_start(self.today)

    # -- page ledger -----------------------------------------------------------------------
    def events_in(self, m: date) -> list:
        from app.models.billable_page_event import BillablePageEvent

        start = datetime(m.year, m.month, 1, tzinfo=IST).astimezone(timezone.utc)
        end_m = add_months(m, 1)
        end = datetime(end_m.year, end_m.month, 1, tzinfo=IST).astimezone(timezone.utc)
        return (self.db.query(BillablePageEvent.tenant_id, BillablePageEvent.shipment_type,
                              BillablePageEvent.page_count, BillablePageEvent.created_at)
                .filter(BillablePageEvent.created_at >= start, BillablePageEvent.created_at < end).all())

    # -- one month ----------------------------------------------------------------------------
    def month(self, m: date) -> dict:
        """{"customers": {tid: {platform, usage, other, pages, jobs}}, "final": bool} - None-free."""
        if m in self._month:
            return self._month[m]
        from app.models.invoice import Invoice
        from app.models.manual_invoice import ManualInvoice

        per: dict[str, dict] = defaultdict(lambda: {"platform": 0.0, "usage": 0.0, "other": 0.0, "pages": 0, "jobs": 0})
        if m > self.current:
            self._month[m] = {"customers": {}, "final": False, "future": True}
            return self._month[m]
        if m < self.current:
            from app.core.billing import ensure_invoices_finalized

            for tid in self.tenants:
                try:
                    ensure_invoices_finalized(self.db, tid)
                except Exception:  # noqa: BLE001 - a broken config must not hide the rest
                    pass
            for inv in self.db.query(Invoice).filter(Invoice.period_start == m.isoformat()).all():
                r = per[inv.tenant_id]
                r["platform"] += inv.platform_fee or 0
                r["usage"] += inv.usage_charges or 0
                r["pages"] += inv.total_pages or 0
                r["jobs"] += inv.total_jobs or 0
        else:
            from app.core.finance_aggregation import summarize_tenant_bill

            for t in self.tenants.values():
                s = summarize_tenant_bill(self.db, t, m)
                if s.total_pages or s.platform_fee or s.usage_charges:
                    r = per[t.id]
                    r["platform"] += s.platform_fee
                    r["usage"] += s.usage_charges
                    r["pages"] += s.total_pages
                    r["jobs"] += s.total_jobs
        last = m.replace(day=calendar.monthrange(m.year, m.month)[1])
        for mi in (self.db.query(ManualInvoice)
                   .filter(ManualInvoice.status.in_(("issued", "paid")),
                           ManualInvoice.invoice_date >= m.isoformat(), ManualInvoice.invoice_date <= last.isoformat()).all()):
            per[mi.tenant_id]["other"] += mi.subtotal or 0
        self._month[m] = {"customers": dict(per), "final": m < self.current, "future": False}
        return self._month[m]

    def totals(self, m: date) -> dict:
        mm = self.month(m)
        if mm.get("future"):
            return {"platform": None, "usage": None, "other": None, "revenue": None, "pages": None, "customers": None}
        c = mm["customers"].values()
        platform = round(sum(x["platform"] for x in c), 2)
        usage = round(sum(x["usage"] for x in c), 2)
        other = round(sum(x["other"] for x in c), 2)
        return {"platform": platform, "usage": usage, "other": other, "revenue": round(platform + usage + other, 2),
                "pages": sum(x["pages"] for x in c),
                "customers": sum(1 for x in c if x["platform"] or x["usage"] or x["other"])}

    def by_shipment(self, m: date) -> dict[str, dict]:
        """Pages and page-usage revenue per shipment type for a month (revenue split by each
        customer's pages that month)."""
        mm = self.month(m)
        out = {k: {"pages": 0, "revenue": 0.0} for k in SHIPMENT_TYPES + ["Other"]}
        if mm.get("future"):
            return out
        pages: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for tid, stype, pc, _at in self.events_in(m):
            pages[tid][_norm_type(stype)] += pc or 0
        for tid, row in mm["customers"].items():
            tp = sum(pages[tid].values())
            for k, n in pages[tid].items():
                out[k]["pages"] += n
                if tp:
                    out[k]["revenue"] += row["usage"] * n / tp
        for k in out:
            out[k]["revenue"] = round(out[k]["revenue"], 2)
        return out


def first_activity(db: Session, ledger: Ledger) -> dict[str, date]:
    """When each customer started: billing start date, else its first billable page."""
    from sqlalchemy import func

    from app.models.billable_page_event import BillablePageEvent
    from app.models.tenant_billing_config import TenantBillingConfig

    out: dict[str, date] = {}
    for tid, first in (db.query(BillablePageEvent.tenant_id, func.min(BillablePageEvent.created_at))
                       .group_by(BillablePageEvent.tenant_id).all()):
        if first:
            out[tid] = (first if first.tzinfo else first.replace(tzinfo=timezone.utc)).astimezone(IST).date()
    for cfg in db.query(TenantBillingConfig).all():
        if cfg.billing_start_date:
            try:
                out[cfg.tenant_id] = date.fromisoformat(cfg.billing_start_date)
            except ValueError:
                pass
    return {tid: d for tid, d in out.items() if tid in ledger.tenants}


def _pct(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round(100.0 * (now - before) / before, 1)


def overview(db: Session) -> dict:
    lg = Ledger(db)
    cur, prev = lg.current, add_months(lg.current, -1)
    t_cur, t_prev, t_prev2 = lg.totals(cur), lg.totals(prev), lg.totals(add_months(cur, -2))
    fy = fy_start_year(lg.today)
    fy_months = [date(fy, 4, 1)]
    while fy_months[-1] < cur:
        fy_months.append(add_months(fy_months[-1], 1))
    ly_months = [date(m.year - 1, m.month, 1) for m in fy_months]
    fytd = round(sum((lg.totals(m)["revenue"] or 0) for m in fy_months), 2)
    lytd = round(sum((lg.totals(m)["revenue"] or 0) for m in ly_months), 2)
    starts = first_activity(db, lg)
    active = [tid for tid, t in lg.tenants.items() if t.is_active and tid in starts]
    new_fy = [tid for tid in active if starts[tid] >= date(fy, 4, 1)]

    from app.core.finance_aggregation import total_outstanding_amount
    from app.models.manual_invoice import ManualInvoice

    from sqlalchemy import func

    manual_due = db.query(func.sum(ManualInvoice.total_amount)).filter(ManualInvoice.status == "issued").scalar() or 0
    paying_prev = t_prev["customers"] or 0

    # milestones
    history_months = []
    m = month_start(min(starts.values())) if starts else cur
    while m <= cur:
        history_months.append(m)
        m = add_months(m, 1)
    hist = [(m, lg.totals(m)) for m in history_months]
    record = max(hist, key=lambda h: h[1]["revenue"] or 0) if hist else None
    first_lakh = next((m for m, t in hist if (t["revenue"] or 0) >= 100000), None)
    total_rev = round(sum(t["revenue"] or 0 for _m, t in hist), 2)
    total_pages = sum(t["pages"] or 0 for _m, t in hist)
    first_customer = min(starts.values()) if starts else None
    milestones = []
    if first_customer:
        milestones.append({"key": "first_customer", "label": "First customer live", "value": first_customer.isoformat()})
    if record and (record[1]["revenue"] or 0) > 0:
        milestones.append({"key": "record_month", "label": "Record month", "value": record[0].strftime("%Y-%m"),
                           "amount": record[1]["revenue"]})
    if first_lakh:
        milestones.append({"key": "first_lakh", "label": "First ₹1 lakh month", "value": first_lakh.strftime("%Y-%m")})
    milestones.append({"key": "total_revenue", "label": "Revenue to date", "amount": total_rev})
    milestones.append({"key": "total_pages", "label": "Pages processed to date", "count": total_pages})

    return {
        "as_of": lg.today.isoformat(),
        "current_month": cur.strftime("%Y-%m"),
        "fy": fy_label(fy),
        "kpis": {
            "revenue_month": t_cur["revenue"], "revenue_prev_month": t_prev["revenue"],
            "mom_pct": _pct(t_prev["revenue"], t_prev2["revenue"]),     # last FULL month vs the one before
            "month_to_date_pct_of_last": _pct(t_cur["revenue"], t_prev["revenue"]),
            "arr": round((t_prev["revenue"] or 0) * 12, 2),
            "fy_to_date": fytd, "fy_to_date_last_year": lytd, "fy_growth_pct": _pct(fytd, lytd),
            "active_customers": len(active), "new_customers_fy": len(new_fy),
            "pages_month": t_cur["pages"], "pages_prev_month": t_prev["pages"],
            "pages_pct": _pct(t_cur["pages"], t_prev["pages"]),
            "arpc": round((t_prev["revenue"] or 0) / paying_prev, 2) if paying_prev else None,
            "outstanding": round(total_outstanding_amount(db) + float(manual_due), 2),
        },
        "milestones": milestones,
    }


def monthly(db: Session, fy: int) -> dict:
    lg = Ledger(db)

    def series(start_year: int) -> list[dict]:
        out = []
        for i in range(12):
            m = add_months(date(start_year, 4, 1), i)
            t = lg.totals(m)
            out.append({"month": m.strftime("%Y-%m"), "label": m.strftime("%b"), **t,
                        "partial": m == lg.current})
        return out

    this, last = series(fy), series(fy - 1)
    starts = first_activity(db, lg)
    cum = []
    for row in this:
        m = date.fromisoformat(row["month"] + "-01")
        if m > lg.current:
            cum.append({"month": row["month"], "label": row["label"], "customers": None, "new": None})
            continue
        end = add_months(m, 1)
        cum.append({"month": row["month"], "label": row["label"],
                    "customers": sum(1 for d in starts.values() if d < end),
                    "new": sum(1 for d in starts.values() if m <= d < end)})
    by_ship = {k: [] for k in SHIPMENT_TYPES}
    for row in this:
        m = date.fromisoformat(row["month"] + "-01")
        b = lg.by_shipment(m) if m <= lg.current else None
        for k in SHIPMENT_TYPES:
            by_ship[k].append(None if b is None else {"pages": b[k]["pages"], "revenue": b[k]["revenue"]})
    total = lambda rows, key: round(sum((r[key] or 0) for r in rows), 2)  # noqa: E731
    years = sorted({fy_start_year(d) for d in starts.values()} | {fy_start_year(lg.today)})
    return {
        "fy": fy_label(fy), "fy_start_year": fy, "available_years": [{"start_year": y, "label": fy_label(y)} for y in years],
        "months": this, "last_year": last,
        "totals": {"revenue": total(this, "revenue"), "platform": total(this, "platform"), "usage": total(this, "usage"),
                   "other": total(this, "other"), "pages": int(total(this, "pages")),
                   "last_year_revenue": total(last, "revenue")},
        "customers": cum, "by_shipment": by_ship,
    }


def daily(db: Session, m: date) -> dict:
    lg = Ledger(db)
    days = calendar.monthrange(m.year, m.month)[1]
    mm = lg.month(m)
    per_day = [{"day": i + 1, "date": m.replace(day=i + 1).isoformat(), "pages": 0, "revenue": 0.0,
                **{k: 0 for k in SHIPMENT_TYPES}} for i in range(days)]
    if not mm.get("future"):
        tenant_day: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for tid, stype, pc, at in lg.events_in(m):
            at = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(IST)
            d = at.day - 1
            per_day[d]["pages"] += pc or 0
            k = _norm_type(stype)
            if k in SHIPMENT_TYPES:
                per_day[d][k] += pc or 0
            tenant_day[tid][d] += pc or 0
        for tid, row in mm["customers"].items():
            tp = sum(tenant_day[tid].values())
            if not tp:
                continue
            for d, n in tenant_day[tid].items():
                per_day[d]["revenue"] += row["usage"] * n / tp
        for r in per_day:
            r["revenue"] = round(r["revenue"], 2)
        if m == lg.current:
            for r in per_day:
                if r["day"] > lg.today.day:
                    r["revenue"] = None
                    r["pages"] = None
    t = lg.totals(m)
    prev = lg.totals(add_months(m, -1))
    starts = first_activity(db, lg)
    earliest = month_start(min(starts.values())) if starts else m
    return {"month": m.strftime("%Y-%m"), "label": m.strftime("%B %Y"), "days": per_day, "totals": t,
            "prev_month_totals": prev, "partial": m == lg.current,
            "earliest_month": earliest.strftime("%Y-%m"), "current_month": lg.current.strftime("%Y-%m")}


def customers_table(db: Session, m: date) -> dict:
    lg = Ledger(db)
    cur, prev = lg.month(m), lg.month(add_months(m, -1))
    total = sum(r["platform"] + r["usage"] + r["other"] for r in cur["customers"].values()) or 0
    rows = []
    for tid, t in lg.tenants.items():
        c = cur["customers"].get(tid)
        p = prev["customers"].get(tid)
        rev = round((c["platform"] + c["usage"] + c["other"]) if c else 0, 2)
        prev_rev = round((p["platform"] + p["usage"] + p["other"]) if p else 0, 2)
        if not rev and not prev_rev:
            continue
        rows.append({"tenant_id": tid, "name": t.name, "is_active": t.is_active, "revenue": rev,
                     "platform": round(c["platform"], 2) if c else 0, "usage": round(c["usage"], 2) if c else 0,
                     "other": round(c["other"], 2) if c else 0, "pages": c["pages"] if c else 0,
                     "prev_revenue": prev_rev, "growth_pct": _pct(rev, prev_rev),
                     "share_pct": round(100 * rev / total, 1) if total else 0})
    rows.sort(key=lambda r: -r["revenue"])
    return {"month": m.strftime("%Y-%m"), "rows": rows, "total": round(total, 2)}


def products(db: Session, fy: int) -> dict:
    """Both product views for a financial year: revenue type and shipment type."""
    mon = monthly(db, fy)
    t = mon["totals"]
    ship = {k: {"pages": sum((x or {}).get("pages", 0) for x in v), "revenue": round(sum((x or {}).get("revenue", 0) for x in v), 2)}
            for k, v in mon["by_shipment"].items()}
    return {"fy": mon["fy"],
            "by_revenue_type": [{"key": "Platform fee", "revenue": t["platform"]}, {"key": "Page usage", "revenue": t["usage"]},
                                {"key": "Other services", "revenue": t["other"]}],
            "by_shipment_type": [{"key": k, **ship[k]} for k in SHIPMENT_TYPES]}
