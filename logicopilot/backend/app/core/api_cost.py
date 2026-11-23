"""Daily OpenAI cost and page usage (Super Admin -> API Cost & Usage).

Every night at 11:55 PM IST the worker collects the day: OpenAI's REAL cost per project (Admin
API, never a token estimate), tokens and requests, today's USD->INR rate (live), and the
platform's page figures from the billing ledger. The record is PROVISIONAL - OpenAI finishes a
day's figures hours later - and the next morning's reconciliation re-reads the last few days
and updates the same rows, marking them FINAL once OpenAI's number has settled.

A "day" is OpenAI's billing day, 00:00-24:00 UTC (05:30 IST to 05:30 IST next day): OpenAI
reports cost only in whole UTC days, so pages are counted over exactly the same window -
otherwise cost per page would divide one window's cost by another window's pages.

Duplicates cannot happen: (usage_date, project_id) and (usage_date, tenant_id) are unique and
a run updates what is there. Every run is logged (api_cost_runs) with its outcome; a failed day
is retried by the next reconciliation or the Retry button. Google Document AI is not included.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
DAILY_AT = dtime(23, 55)          # IST - collect today (provisional)
RECONCILE_AT = dtime(11, 0)       # IST - re-read the last RECONCILE_DAYS days
RECONCILE_DAYS = 3
FINAL_AFTER_HOURS = 6             # a finished day whose cost did not move between reads is final
FINAL_ANYWAY_AFTER_HOURS = 48     # ...and any finished day is final after this
MAX_MANUAL_DAYS = 62


# ---------------------------------------------------------------------------- inputs

def usd_inr_rate(day: date, db: Session | None = None) -> tuple[float | None, str]:
    """(rate, source). The day's ECB reference rate (frankfurter.app), then open.er-api.com,
    then the last rate this table stored. A day without any rate keeps its USD and no INR."""
    tries = [
        (f"https://api.frankfurter.app/{'latest' if day >= date.today() else day.isoformat()}?from=USD&to=INR",
         "frankfurter.app (ECB)", lambda b: b["rates"]["INR"]),
        ("https://open.er-api.com/v6/latest/USD", "open.er-api.com", lambda b: b["rates"]["INR"]),
    ]
    for url, source, pick in tries:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cargora-api-cost/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rate = float(pick(json.loads(r.read())))
            if 40 < rate < 200:                     # sanity: never store a broken figure
                return rate, source
        except Exception:  # noqa: BLE001
            logger.warning("USD->INR rate from %s failed", source, exc_info=True)
    if db is not None:
        from app.models.api_cost import ApiCostDaily

        last = (db.query(ApiCostDaily.usd_inr_rate, ApiCostDaily.usage_date)
                .filter(ApiCostDaily.usd_inr_rate.isnot(None))
                .order_by(ApiCostDaily.usage_date.desc()).first())
        if last:
            return float(last[0]), f"last known rate ({last[1].isoformat()})"
    return None, "unavailable"


def openai_day(api_key: str, day: date) -> dict:
    """{project_id: {"name", "usd", "input_tokens", "output_tokens", "requests"}} for one UTC day."""
    from app.core.openai_admin import _bucket_day, _paged_buckets, fetch_projects

    names = fetch_projects(api_key)
    out: dict[str, dict] = {}

    def row(pid):
        return out.setdefault(pid, {"name": names.get(pid, pid), "usd": 0.0, "input_tokens": 0,
                                    "output_tokens": 0, "requests": 0})

    for b in _paged_buckets("/organization/costs", api_key, day, day, group_by=["project_id"]):
        if _bucket_day(b["start_time"]) != day.isoformat():
            continue
        for r in b.get("results", []):
            row(r.get("project_id") or "unknown")["usd"] += float((r.get("amount") or {}).get("value") or 0)
    for b in _paged_buckets("/organization/usage/completions", api_key, day, day, group_by=["project_id"]):
        if _bucket_day(b["start_time"]) != day.isoformat():
            continue
        for r in b.get("results", []):
            x = row(r.get("project_id") or "unknown")
            x["input_tokens"] += int(r.get("input_tokens") or 0)
            x["output_tokens"] += int(r.get("output_tokens") or 0)
            x["requests"] += int(r.get("num_model_requests") or 0)
    return out


def _window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, dtime.min, timezone.utc)
    return start, start + timedelta(days=1)


def page_stats(db: Session, day: date) -> tuple[dict, dict[str, dict]]:
    """(platform totals, {tenant_id: figures}) for one OpenAI day.

    Pages billed / original / re-run: the billing ledger (every page read, deleted jobs too).
    Jobs: distinct jobs with pages billed that day. Classified / unclassified pages: of those
    jobs that still exist, pages placed in a document slot (they go on to extraction) vs pages
    left in Unclassified (not extracted)."""
    from app.models.billable_page_event import BillablePageEvent as E
    from app.models.job import Job, JobDocument, UnmatchedUploadPage
    from app.models.tenant import Tenant

    start, end = _window(day)
    events = (db.query(E.tenant_id, E.job_id, E.page_count, E.is_rerun)
              .filter(E.created_at >= start, E.created_at < end).all())
    per: dict[str, dict] = {}
    jobs_by_tenant: dict[str, set] = {}
    for tenant_id, job_id, pages, rerun in events:
        t = per.setdefault(tenant_id, {"pages_billed": 0, "original_pages": 0, "rerun_pages": 0,
                                       "classified_pages": 0, "unclassified_pages": 0, "total_jobs": 0})
        t["pages_billed"] += pages or 0
        t["rerun_pages" if rerun else "original_pages"] += pages or 0
        jobs_by_tenant.setdefault(tenant_id, set()).add(job_id)
    all_jobs = {j for s in jobs_by_tenant.values() for j in s}
    if all_jobs:
        live = {jid: tid for jid, tid in db.query(Job.id, Job.tenant_id).filter(Job.id.in_(all_jobs)).all()}
        for jid, n in (db.query(JobDocument.job_id, func.coalesce(func.sum(JobDocument.page_count), 0))
                       .filter(JobDocument.job_id.in_(live), JobDocument.file_path.isnot(None))
                       .group_by(JobDocument.job_id).all()):
            per[live[jid]]["classified_pages"] += int(n or 0)
        for jid, n in (db.query(UnmatchedUploadPage.job_id, func.count(UnmatchedUploadPage.id))
                       .filter(UnmatchedUploadPage.job_id.in_(live))
                       .group_by(UnmatchedUploadPage.job_id).all()):
            per[live[jid]]["unclassified_pages"] += int(n or 0)
    for tid, s in jobs_by_tenant.items():
        per[tid]["total_jobs"] = len(s)
    names = dict(db.query(Tenant.id, Tenant.name).filter(Tenant.id.in_(per or {""})).all())
    for tid in per:
        per[tid]["tenant_name"] = names.get(tid)
    keys = ("pages_billed", "original_pages", "rerun_pages", "classified_pages", "unclassified_pages", "total_jobs")
    total = {k: sum(v[k] for v in per.values()) for k in keys}
    return total, per


# ---------------------------------------------------------------------------- one day

def _status(day: date, old_usd: float | None, new_usd: float, kind: str) -> str:
    if kind == "daily":
        return "provisional"
    ended = _window(day)[1]
    hours = (datetime.now(timezone.utc) - ended).total_seconds() / 3600
    if hours >= FINAL_ANYWAY_AFTER_HOURS:
        return "final"
    if hours >= FINAL_AFTER_HOURS and old_usd is not None and abs(old_usd - new_usd) < 0.0001:
        return "final"
    return "provisional"


def record_day(db: Session, day: date, kind: str, api_key: str) -> dict:
    """Collect one day and save / update its rows. Raises on an OpenAI failure (the caller logs it)."""
    from app.models.api_cost import TOTAL_PROJECT, ApiCostDaily, ApiCostDailyCustomer

    projects = openai_day(api_key, day)
    rate, source = usd_inr_rate(day, db)
    total, per_tenant = page_stats(db, day)
    now = datetime.now(timezone.utc)
    existing = {r.project_id: r for r in db.query(ApiCostDaily).filter(ApiCostDaily.usage_date == day).all()}
    old_total = existing.get(TOTAL_PROJECT)
    usd_total = round(sum(p["usd"] for p in projects.values()), 6)
    status = _status(day, old_total.cost_usd if old_total and old_total.status != "failed" else None, usd_total, kind)

    def upsert(pid: str, **values) -> None:
        row = existing.get(pid)
        if row is None:
            row = ApiCostDaily(usage_date=day, project_id=pid)
            db.add(row)
        for k, v in values.items():
            setattr(row, k, v)
        row.status, row.fetched_at = status, now
        if kind != "daily":
            row.reconciled_at = now

    for pid, p in projects.items():
        upsert(pid, project_name=p["name"], cost_usd=round(p["usd"], 6), input_tokens=p["input_tokens"],
               output_tokens=p["output_tokens"], requests=p["requests"], usd_inr_rate=rate, rate_source=source,
               cost_inr=round(p["usd"] * rate, 4) if rate else None)
    pages = total["pages_billed"]
    upsert(TOTAL_PROJECT, project_name="Account total", cost_usd=usd_total,
           input_tokens=sum(p["input_tokens"] for p in projects.values()),
           output_tokens=sum(p["output_tokens"] for p in projects.values()),
           requests=sum(p["requests"] for p in projects.values()),
           usd_inr_rate=rate, rate_source=source, cost_inr=round(usd_total * rate, 4) if rate else None,
           cost_per_page_usd=round(usd_total / pages, 6) if pages else None,
           cost_per_page_inr=round(usd_total * rate / pages, 4) if pages and rate else None,
           raw={"projects": projects}, **total)
    have = {r.tenant_id: r for r in db.query(ApiCostDailyCustomer).filter(ApiCostDailyCustomer.usage_date == day).all()}
    for tid, v in per_tenant.items():
        row = have.get(tid) or ApiCostDailyCustomer(usage_date=day, tenant_id=tid)
        if tid not in have:
            db.add(row)
        for k, val in v.items():
            setattr(row, k, val)
    db.commit()
    return {"day": day.isoformat(), "usd": usd_total, "inr_rate": rate, "pages": pages, "status": status,
            "projects": len(projects)}


def _mark_failed(db: Session, day: date, error: str) -> None:
    """A day with nothing collected yet gets a FAILED total row, so the gap shows on the screen.
    A day already collected keeps its figures (the failure is in the run log)."""
    from app.models.api_cost import TOTAL_PROJECT, ApiCostDaily

    db.rollback()
    if db.query(ApiCostDaily).filter(ApiCostDaily.usage_date == day, ApiCostDaily.project_id == TOTAL_PROJECT).first():
        return
    db.add(ApiCostDaily(usage_date=day, project_id=TOTAL_PROJECT, project_name="Account total",
                        status="failed", fetched_at=datetime.now(timezone.utc), raw={"error": error[:500]}))
    db.commit()


def run(days: list[date], kind: str, triggered_by: str | None = None) -> dict:
    """Collect the given days, logging the run. Never raises."""
    from app.core.system_settings import get_openai_admin_key
    from app.db.session import SessionLocal
    from app.models.api_cost import ApiCostRun

    db = SessionLocal()
    log = ApiCostRun(kind=kind, usage_dates=",".join(d.isoformat() for d in days)[:255], status="running",
                     started_at=datetime.now(timezone.utc), triggered_by=triggered_by)
    db.add(log)
    db.commit()
    done, errors = [], []
    try:
        key = get_openai_admin_key(db)
        if not key:
            raise RuntimeError("No OpenAI Admin key is saved (Platform Settings) - real cost cannot be read.")
        for day in days:
            try:
                done.append(record_day(db, day, kind, key))
            except Exception as exc:  # noqa: BLE001 - one day failing must not stop the others
                logger.exception("API cost: %s failed for %s", kind, day)
                errors.append(f"{day.isoformat()}: {exc}"[:300])
                _mark_failed(db, day, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("API cost run failed")
        errors.append(str(exc)[:500])
        for day in days:
            _mark_failed(db, day, str(exc))
    log = db.get(ApiCostRun, log.id)
    log.status = "failed" if errors else "ok"
    log.error = "\n".join(errors) or None
    log.summary = {"days": done}
    log.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.close()
    logger.info("API cost %s run: %d day(s) ok, %d error(s)", kind, len(done), len(errors))
    return {"status": "failed" if errors else "ok", "days": done, "errors": errors}


# ---------------------------------------------------------------------------- scheduler

def _claim(key: str) -> bool:
    """One process, once: Redis SET NX when Redis answers, else the run log."""
    try:
        from app.core.erp_redis_queue import _redis

        r = _redis()
        if r is not None:
            return bool(r.set(f"apicost:{key}", "1", nx=True, ex=3 * 86400))
    except Exception:  # noqa: BLE001
        logger.warning("API cost: Redis lock unavailable - falling back to the run log", exc_info=True)
    from app.db.session import SessionLocal
    from app.models.api_cost import ApiCostRun

    kind, ist_day = key.split(":", 1)
    db = SessionLocal()
    try:
        start = datetime.combine(date.fromisoformat(ist_day), dtime.min, IST)
        seen = (db.query(ApiCostRun.id).filter(ApiCostRun.kind == kind, ApiCostRun.started_at >= start,
                                                ApiCostRun.started_at < start + timedelta(days=1)).first())
        return seen is None
    finally:
        db.close()


def tick(now: datetime | None = None) -> str | None:
    """What the scheduler does once a minute. Returns which job it started, if any."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    today_ist = now.date()
    if now.time() >= DAILY_AT and _claim(f"daily:{today_ist.isoformat()}"):
        # At 23:55 IST the OpenAI (UTC) day with today's date is still running - provisional.
        run([now.astimezone(timezone.utc).date()], "daily", "scheduler")
        return "daily"
    if now.time() >= RECONCILE_AT and _claim(f"reconcile:{today_ist.isoformat()}"):
        utc_today = now.astimezone(timezone.utc).date()
        run([utc_today - timedelta(days=i) for i in range(RECONCILE_DAYS, 0, -1)], "reconcile", "scheduler")
        return "reconcile"
    return None


_started = False


def start_scheduler() -> None:
    """Worker process only (main.py), when API_COST_RECORDING is on. Idempotent."""
    global _started
    if _started:
        return
    _started = True

    def _loop() -> None:
        time.sleep(30)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001 - the loop must survive anything
                logger.exception("API cost scheduler tick failed")
            time.sleep(60)

    threading.Thread(target=_loop, daemon=True, name="api-cost-scheduler").start()
    logger.info("API cost scheduler started: collect %s IST, reconcile %s IST", DAILY_AT, RECONCILE_AT)


# ---------------------------------------------------------------------------- reading

def _row(r) -> dict:
    return {c: getattr(r, c) for c in (
        "project_id", "project_name", "cost_usd", "input_tokens", "output_tokens", "requests",
        "usd_inr_rate", "rate_source", "cost_inr", "pages_billed", "original_pages", "rerun_pages",
        "classified_pages", "unclassified_pages", "total_jobs", "cost_per_page_usd",
        "cost_per_page_inr", "status")} | {
        "usage_date": r.usage_date.isoformat(),
        "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
        "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None}


def daily(db: Session, month: str) -> dict:
    from app.models.api_cost import TOTAL_PROJECT, ApiCostDaily, ApiCostDailyCustomer

    first = date.fromisoformat(f"{month}-01")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = (db.query(ApiCostDaily).filter(ApiCostDaily.project_id == TOTAL_PROJECT,
                                          ApiCostDaily.usage_date >= first, ApiCostDaily.usage_date < nxt)
            .order_by(ApiCostDaily.usage_date).all())
    cust = (db.query(ApiCostDailyCustomer).filter(ApiCostDailyCustomer.usage_date >= first,
                                                  ApiCostDailyCustomer.usage_date < nxt).all())
    by_day: dict[str, list] = {}
    for c in cust:
        by_day.setdefault(c.usage_date.isoformat(), []).append(
            {"tenant_id": c.tenant_id, "tenant_name": c.tenant_name, "pages_billed": c.pages_billed,
             "original_pages": c.original_pages, "rerun_pages": c.rerun_pages,
             "classified_pages": c.classified_pages, "unclassified_pages": c.unclassified_pages,
             "total_jobs": c.total_jobs})
    days = [_row(r) | {"customers": by_day.get(r.usage_date.isoformat(), [])} for r in rows]
    return {"month": month, "days": days, "totals": _sum(days)}


def _sum(days: list[dict]) -> dict:
    keys = ("cost_usd", "cost_inr", "pages_billed", "original_pages", "rerun_pages",
            "classified_pages", "unclassified_pages", "total_jobs", "input_tokens", "output_tokens", "requests")
    t = {k: round(sum((d.get(k) or 0) for d in days), 4) for k in keys}
    t["cost_per_page_usd"] = round(t["cost_usd"] / t["pages_billed"], 6) if t["pages_billed"] else None
    t["cost_per_page_inr"] = round(t["cost_inr"] / t["pages_billed"], 4) if t["pages_billed"] and t["cost_inr"] else None
    t["provisional_days"] = sum(1 for d in days if d.get("status") == "provisional")
    t["failed_days"] = sum(1 for d in days if d.get("status") == "failed")
    return t


def monthly(db: Session, months: int = 12) -> dict:
    from app.models.api_cost import TOTAL_PROJECT, ApiCostDaily, ApiCostDailyCustomer

    rows = (db.query(ApiCostDaily).filter(ApiCostDaily.project_id == TOTAL_PROJECT)
            .order_by(ApiCostDaily.usage_date).all())
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.usage_date.strftime("%Y-%m"), []).append(_row(r))
    cust: dict[tuple, dict] = {}
    for c in db.query(ApiCostDailyCustomer).all():
        k = (c.usage_date.strftime("%Y-%m"), c.tenant_id)
        x = cust.setdefault(k, {"tenant_id": c.tenant_id, "tenant_name": c.tenant_name, "pages_billed": 0,
                                "original_pages": 0, "rerun_pages": 0, "total_jobs": 0})
        for f in ("pages_billed", "original_pages", "rerun_pages", "total_jobs"):
            x[f] += getattr(c, f) or 0
    out = []
    for m in sorted(groups, reverse=True)[:months]:
        out.append({"month": m, "days_recorded": len(groups[m]), **_sum(groups[m]),
                    "customers": [v for (mm, _), v in cust.items() if mm == m]})
    return {"months": out}


def day_detail(db: Session, day: date) -> dict:
    from app.models.api_cost import ApiCostDaily, ApiCostRun

    rows = db.query(ApiCostDaily).filter(ApiCostDaily.usage_date == day).order_by(ApiCostDaily.cost_usd.desc()).all()
    runs = (db.query(ApiCostRun).filter(ApiCostRun.usage_dates.contains(day.isoformat()))
            .order_by(ApiCostRun.started_at.desc()).limit(20).all())
    return {"day": day.isoformat(), "projects": [_row(r) for r in rows],
            "runs": [{"kind": r.kind, "status": r.status, "error": r.error, "triggered_by": r.triggered_by,
                      "started_at": r.started_at.isoformat(),
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs]}
