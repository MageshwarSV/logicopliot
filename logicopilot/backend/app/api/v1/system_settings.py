import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.currency import CurrencyRateError, fetch_usd_to_inr_rate
from app.core.deps import get_db, require_role
from app.core.openai_admin import (
    OpenAIAdminAPIError,
    fetch_daily_costs,
    fetch_daily_costs_by_project,
    fetch_daily_token_usage,
    fetch_projects,
)
from app.core.system_settings import (
    InvalidOpenAIAdminKey,
    InvalidOpenAIBalance,
    InvalidOpenAIKey,
    InvalidWorkerCount,
    clear_openai_admin_key,
    get_openai_admin_key,
    get_system_settings,
    has_custom_openai_key,
    has_openai_admin_key,
    max_email_poll_workers,
    set_email_poll_workers,
    set_openai_admin_key,
    set_openai_api_key,
    set_openai_balance,
)
from app.models.email_seen import EmailSeen
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.user import SUPER_ADMIN, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system-settings", tags=["system settings"])


class PauseFlag(BaseModel):
    paused: bool


class OpenAIKeyIn(BaseModel):
    api_key: str


class WorkerCountIn(BaseModel):
    count: int


class OpenAIBalanceIn(BaseModel):
    balance_usd: float | None = None
    expiry_date: date | None = None


@router.get("")
def read_system_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    row = get_system_settings(db)
    return {
        "email_pull_paused": row.email_pull_paused,
        "extraction_paused": row.extraction_paused,
        # Never the real key, not even masked - a yes/no only. Changing it is write-only,
        # same rule as an operator's own mailbox app password.
        "openai_api_key_set": has_custom_openai_key(db),
        # A SEPARATE key from the one above (see app/core/openai_admin.py) - only ever used
        # to read real spend for Spend Analytics, never to make calls.
        "openai_admin_key_set": has_openai_admin_key(db),
        "email_poll_workers": row.email_poll_workers,
        # What THIS server can actually sustain - one thread per CPU core - so the UI can
        # show it as a hard ceiling rather than the admin guessing a safe number.
        "max_email_poll_workers": max_email_poll_workers(),
        # Manually entered - see app/core/system_settings.set_openai_balance's docstring for
        # why this can never come from OpenAI's own API, not even the Admin key.
        "openai_balance_usd": row.openai_balance_usd,
        "openai_balance_expiry": row.openai_balance_expiry.isoformat() if row.openai_balance_expiry else None,
    }


@router.post("/email-pull")
def set_email_pull_paused(
    payload: PauseFlag,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Stop the email poller - scheduled AND manual "Check mail" - before it ever connects
    to a mailbox. Turning it ON also clears any message currently mid-read (verdict
    "working"): that message was opened but nothing was completed for it, so it is left
    exactly as available as it was before this poll ever touched it, and logged as such
    rather than silently blocked from ever being tried again. Turning it back off needs
    nothing further - the very next pull just runs.
    """
    row = get_system_settings(db)
    row.email_pull_paused = payload.paused
    cleared = 0
    if payload.paused:
        stuck = db.query(EmailSeen).filter(EmailSeen.verdict == "working").all()
        for r in stuck:
            logger.warning(
                "email pull paused: message %s (subject: %r) was opened but nothing was "
                "completed for it - clearing the claim so it is retried once resumed",
                r.message_id[:80], r.subject)
            db.delete(r)
            cleared += 1
    db.commit()
    return {"email_pull_paused": row.email_pull_paused, "cleared_in_progress": cleared}


@router.post("/extraction")
def set_extraction_paused(
    payload: PauseFlag,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Stop document field extraction - the Extract button, auto-extract after upload, and
    the email puller's own post-classification extraction step - before any OCR/AI call.
    A job already mid-extraction when this is turned on is left to the existing safety net
    (see run_extraction/_start_extraction_background): it reverts to "draft" rather than
    sitting on "AI Processing" forever, and pressing Extract again once resumed is all that
    is needed - nothing to clear here the way email pull's stuck claims need clearing.
    """
    row = get_system_settings(db)
    row.extraction_paused = payload.paused
    db.commit()
    return {"extraction_paused": row.extraction_paused}


@router.get("/mailboxes")
def list_mailboxes(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """Every operator mailbox connected anywhere in the system - Gmail, Zoho, whichever
    provider - across EVERY tenant, since this screen is system-wide (Super Admin only), not
    scoped to one tenant the way the rest of the admin UI is. Each row is exactly what
    _operator_mailboxes (app/core/email_puller.py) would poll if not individually paused -
    same filters (active, has mail credentials), same mail_paused switch shown here for
    toggling. The tenant's own shared inbox (configured in .env, not a User row) has no
    per-row identity to list or pause here.
    """
    from app.models.tenant import Tenant
    from app.models.user import OPERATOR, User

    rows = (
        db.query(User, Tenant.name)
        .outerjoin(Tenant, Tenant.id == User.tenant_id)
        .filter(User.role == OPERATOR, User.mail_email.isnot(None))
        .order_by(Tenant.name, User.full_name)
        .all()
    )
    return [
        {
            "user_id": u.id,
            "full_name": u.full_name,
            "tenant_id": u.tenant_id,
            "tenant_name": tenant_name,
            "mail_provider": u.mail_provider,
            "mail_email": u.mail_email,
            "is_active": u.is_active,
            "mail_paused": u.mail_paused,
        }
        for u, tenant_name in rows
    ]


@router.post("/mailboxes/{user_id}/pause")
def set_mailbox_paused(
    user_id: str,
    payload: PauseFlag,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Stop (or resume) polling ONE operator's mailbox, independent of every other mailbox
    and of the system-wide email_pull_paused switch above. Applies on the very next poll
    cycle - nothing further to clear, the same as extraction's own pause."""
    from app.models.user import User

    user = db.get(User, user_id)
    if user is None or not user.mail_email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such connected mailbox.")
    user.mail_paused = payload.paused
    db.commit()
    return {"user_id": user.id, "mail_paused": user.mail_paused}


@router.post("/openai-admin-key")
def set_openai_admin_key_endpoint(
    payload: OpenAIKeyIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """A SEPARATE key from /openai-key above: OpenAI's own "Admin API key" (Organization >
    Admin keys on platform.openai.com), the only key type OpenAI permits to read
    organization-level cost/usage data. Verified live (a real, free call to the costs
    endpoint) before saving, same principle as the regular key. Never applied as the calling
    key - this one is for reading spend only.
    """
    try:
        set_openai_admin_key(db, payload.api_key)
    except InvalidOpenAIAdminKey as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"OpenAI rejected this Admin key: {exc}")
    return {"openai_admin_key_set": has_openai_admin_key(db)}


@router.delete("/openai-admin-key")
def delete_openai_admin_key_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    clear_openai_admin_key(db)
    return {"openai_admin_key_set": False}


class OpenAICostDay(BaseModel):
    date: str
    usd: float
    input_tokens: int
    output_tokens: int


class OpenAICostsOut(BaseModel):
    connected: bool
    start: str
    end: str
    total_usd: float
    total_input_tokens: int
    total_output_tokens: int
    days: list[OpenAICostDay]


@router.get("/openai-costs", response_model=OpenAICostsOut)
def openai_costs(
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> OpenAICostsOut:
    """The REAL dollar figure and real token counts, straight from OpenAI's own organization
    Admin API - not the estimate spend-analytics below computes from this app's own data.
    connected=False (never an error) when no Admin key has been saved yet - the Spend
    Analytics page falls back to showing only its own local counts in that case.
    """
    try:
        end_inclusive = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
        start_date = date.fromisoformat(start) if start else end_inclusive - timedelta(days=29)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start/end must be YYYY-MM-DD dates")
    if start_date > end_inclusive:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start must not be after end")

    admin_key = get_openai_admin_key(db)
    if not admin_key:
        return OpenAICostsOut(
            connected=False, start=start_date.isoformat(), end=end_inclusive.isoformat(),
            total_usd=0.0, total_input_tokens=0, total_output_tokens=0, days=[],
        )

    try:
        costs = fetch_daily_costs(admin_key, start_date, end_inclusive)
        tokens = fetch_daily_token_usage(admin_key, start_date, end_inclusive)
    except OpenAIAdminAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not reach OpenAI: {exc}")

    all_days = sorted(set(costs) | set(tokens))
    days = [
        OpenAICostDay(
            date=d, usd=round(costs.get(d, 0.0), 4),
            input_tokens=tokens.get(d, {}).get("input_tokens", 0),
            output_tokens=tokens.get(d, {}).get("output_tokens", 0),
        )
        for d in all_days
    ]
    return OpenAICostsOut(
        connected=True, start=start_date.isoformat(), end=end_inclusive.isoformat(),
        total_usd=round(sum(d.usd for d in days), 4),
        total_input_tokens=sum(d.input_tokens for d in days),
        total_output_tokens=sum(d.output_tokens for d in days),
        days=days,
    )


class OpenAICostByProject(BaseModel):
    project_id: str
    project_name: str
    usd: float


class OpenAICostsByProjectOut(BaseModel):
    connected: bool
    start: str
    end: str
    total_usd: float
    projects: list[OpenAICostByProject]


@router.get("/openai-costs-by-project", response_model=OpenAICostsByProjectOut)
def openai_costs_by_project(
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> OpenAICostsByProjectOut:
    """The same real org-wide spend /openai-costs reports, broken out per OpenAI project -
    this server runs several apps under what may be one shared OpenAI organization, so the
    combined total above can silently include spend that has nothing to do with this app.
    This tells the pieces apart by the project each API key actually belongs to."""
    try:
        end_inclusive = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
        start_date = date.fromisoformat(start) if start else end_inclusive - timedelta(days=29)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start/end must be YYYY-MM-DD dates")
    if start_date > end_inclusive:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start must not be after end")

    admin_key = get_openai_admin_key(db)
    if not admin_key:
        return OpenAICostsByProjectOut(
            connected=False, start=start_date.isoformat(), end=end_inclusive.isoformat(),
            total_usd=0.0, projects=[],
        )

    try:
        by_project = fetch_daily_costs_by_project(admin_key, start_date, end_inclusive)
        names = fetch_projects(admin_key)
    except OpenAIAdminAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Could not reach OpenAI: {exc}")

    projects = [
        OpenAICostByProject(
            project_id=pid, project_name=names.get(pid, pid),
            usd=round(sum(day_totals.values()), 4),
        )
        for pid, day_totals in by_project.items()
    ]
    projects.sort(key=lambda p: p.usd, reverse=True)
    return OpenAICostsByProjectOut(
        connected=True, start=start_date.isoformat(), end=end_inclusive.isoformat(),
        total_usd=round(sum(p.usd for p in projects), 4),
        projects=projects,
    )


@router.post("/openai-key")
def set_openai_key(
    payload: OpenAIKeyIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Verified live against OpenAI itself (listing models costs nothing and needs no
    credits) before it is saved - a typo must never silently replace a working key with a
    dead one. Applied to THIS process immediately on success: the very next AI call
    anywhere in the app - email classification, extraction, everything - uses the new key,
    no restart. Encrypted at rest; the plaintext is never returned by this or any other
    endpoint again, same rule as an operator's own mailbox app password.
    """
    try:
        set_openai_api_key(db, payload.api_key)
    except InvalidOpenAIKey as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"OpenAI rejected this key: {exc}")
    return {"openai_api_key_set": has_custom_openai_key(db)}


class SpendAnalyticsDay(BaseModel):
    date: str
    jobs: int
    documents: int
    pages: int
    characters: int
    estimated_tokens: int
    # Jobs that reached "extracted" but every field came back empty — an OpenAI/OCR call was
    # made and paid for, with nothing usable to show for it.
    zero_result_jobs: int


class SpendAnalyticsTotals(BaseModel):
    jobs: int
    documents: int
    pages: int
    characters: int
    estimated_tokens: int
    zero_result_jobs: int


class SpendAnalyticsOut(BaseModel):
    start: str
    end: str
    totals: SpendAnalyticsTotals
    days: list[SpendAnalyticsDay]


@router.get("/spend-analytics", response_model=SpendAnalyticsOut)
def spend_analytics(
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> SpendAnalyticsOut:
    """How many jobs, documents, and pages this platform processed, and roughly how many
    OpenAI tokens that cost — platform-wide across every tenant, since this is one shared
    OpenAI account's spend, not a per-tenant figure.

    No endpoint or column anywhere in this system records the actual token count OpenAI
    billed for a call, so "estimated_tokens" is exactly that: the real OCR text this system
    read for each document (job_documents.extracted_json, the same text a run actually sends
    into every AI call) divided by 4, OpenAI's own published rule of thumb for English text.
    It is the closest defensible estimate this database can produce, not the real bill —
    that lives only on platform.openai.com/usage, under whichever account holds the
    configured key.

    `start`/`end` are inclusive calendar dates (YYYY-MM-DD); both default to a 30-day window
    ending today (server UTC).
    """
    try:
        end_inclusive = date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
        start_date = date.fromisoformat(start) if start else end_inclusive - timedelta(days=29)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start/end must be YYYY-MM-DD dates")
    if start_date > end_inclusive:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="start must not be after end")

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_inclusive + timedelta(days=1), datetime.min.time())

    jobs = (
        db.query(Job.id, Job.created_at)
        .filter(Job.created_at >= start_dt, Job.created_at < end_dt)
        .all()
    )
    job_day = {j.id: j.created_at.date() for j in jobs}

    def _new_bucket() -> dict[str, int]:
        return {"jobs": 0, "documents": 0, "pages": 0, "characters": 0, "zero_result_jobs": 0}

    buckets: dict[date, dict[str, int]] = {}
    for day in job_day.values():
        buckets.setdefault(day, _new_bucket())["jobs"] += 1

    if job_day:
        docs = (
            db.query(JobDocument)
            .filter(JobDocument.job_id.in_(list(job_day.keys())), JobDocument.file_path.isnot(None))
            .all()
        )
        for d in docs:
            day = job_day.get(d.job_id)
            if day is None:
                continue
            b = buckets.setdefault(day, _new_bucket())
            b["documents"] += 1
            b["pages"] += d.page_count or 0
            text_val = d.extracted_json.get("text") if isinstance(d.extracted_json, dict) else None
            b["characters"] += len(text_val) if text_val else 0

        # A job "has data" if ANY field value came back non-empty; everything else in the
        # window paid for an extraction and got nothing usable — the number a manager
        # conversation about wasted spend actually needs.
        jobs_with_data = {
            row[0] for row in db.query(JobFieldValue.job_id)
            .filter(JobFieldValue.job_id.in_(list(job_day.keys())),
                    JobFieldValue.extracted_value.isnot(None), JobFieldValue.extracted_value != "")
            .distinct().all()
        }
        for job_id, day in job_day.items():
            if job_id not in jobs_with_data:
                buckets.setdefault(day, _new_bucket())["zero_result_jobs"] += 1

    days = [
        SpendAnalyticsDay(
            date=day.isoformat(), jobs=v["jobs"], documents=v["documents"], pages=v["pages"],
            characters=v["characters"], estimated_tokens=round(v["characters"] / 4),
            zero_result_jobs=v["zero_result_jobs"],
        )
        for day, v in sorted(buckets.items())
    ]
    totals = SpendAnalyticsTotals(
        jobs=sum(d.jobs for d in days),
        documents=sum(d.documents for d in days),
        pages=sum(d.pages for d in days),
        characters=sum(d.characters for d in days),
        estimated_tokens=sum(d.estimated_tokens for d in days),
        zero_result_jobs=sum(d.zero_result_jobs for d in days),
    )
    return SpendAnalyticsOut(
        start=start_date.isoformat(), end=end_inclusive.isoformat(), totals=totals, days=days,
    )


@router.post("/workers")
def set_workers(
    payload: WorkerCountIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """How many mailboxes the email poller reads at once. Capped at this server's own CPU
    count (see max_email_poll_workers) - a number this machine cannot sustain is refused,
    never silently clamped, so the admin knows exactly what limit they hit."""
    try:
        set_email_poll_workers(db, payload.count)
    except InvalidWorkerCount as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"email_poll_workers": get_system_settings(db).email_poll_workers}


@router.post("/openai-balance")
def set_openai_balance_endpoint(
    payload: OpenAIBalanceIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Manually entered - see app/core/system_settings.set_openai_balance's docstring for why
    this can never be read from OpenAI's own API. Send balance_usd=null to clear it."""
    try:
        set_openai_balance(db, payload.balance_usd, payload.expiry_date)
    except InvalidOpenAIBalance as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    row = get_system_settings(db)
    return {
        "openai_balance_usd": row.openai_balance_usd,
        "openai_balance_expiry": row.openai_balance_expiry.isoformat() if row.openai_balance_expiry else None,
    }


class UsdInrRateOut(BaseModel):
    rate: float
    as_of: str


@router.get("/usd-inr-rate", response_model=UsdInrRateOut)
def read_usd_inr_rate(
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> UsdInrRateOut:
    """Live 1 USD -> INR rate (see app/core/currency.py) - used by the frontend to show the
    real OpenAI spend and the manually-entered account balance in INR alongside USD."""
    try:
        rate, as_of = fetch_usd_to_inr_rate()
    except CurrencyRateError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return UsdInrRateOut(rate=rate, as_of=as_of)
