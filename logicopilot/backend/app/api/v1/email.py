from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.email_puller import get_last_cycle_status, pull_inbox, test_connection
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(prefix="/email", tags=["email auto-pull"])


@router.get("/status")
def email_status(_: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN, OPERATOR))):
    """Check the mailbox connection and report total / unread counts."""
    return test_connection()


@router.get("/poll-status")
def email_poll_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
):
    """What the background poller's own most recent tick did - never triggers a pull itself,
    just reports on the last one: how many connected mailboxes there were, how many actually
    got checked, how many failed (and why), and whether that tick ran at all or was skipped
    because the previous cycle was still busy. {} before the poller's first tick completes."""
    return get_last_cycle_status(db)


@router.post("/pull")
def email_pull(
    reexamine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN, OPERATOR)),
):
    """Pull unread messages and auto-create jobs for the customer the DOCUMENTS name.

    - Super Admin: all tenants' customers.
    - Tenant Admin / Operator: their own tenant's customers - every mailbox connected and
      assigned to it (see ConnectedMailbox), not just one person's own mail. A mailbox is
      never owned by one operator any more (see the Email Puller settings screen), so there
      is no narrower "my own mail" to restrict an operator's own press of this button to.

    `reexamine=true` looks again at messages already examined and set aside. Use it after
    configuring a customer who had none: their earlier mail was deliberately left unread for
    exactly this, and is otherwise skipped so the same OCR and model calls are not paid twice.
    """
    if user.role == SUPER_ADMIN:
        return pull_inbox(db, reexamine=reexamine)
    return pull_inbox(db, tenant_id=user.tenant_id, reexamine=reexamine)


# --------------------------------------------------------------------------- #
# Mails held up because the AI service failed - Super Admin only
# --------------------------------------------------------------------------- #
@router.get("/failed")
def list_failed_emails(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    """Every mail that was tried and could not be read because the AI service refused the call
    (no credits, a rate limit, an outage), plus every mail that arrived while the puller was
    holding off for that reason. None of them has a job yet. They are retried automatically
    when the wait ends; POST /email/failed/rerun retries them now."""
    from app.core.email_puller import ai_wait_status, failed_emails
    from app.models.template_group import TemplateGroup

    rows = failed_emails(db)
    names = {
        g.id: g.name
        for g in db.query(TemplateGroup).filter(
            TemplateGroup.id.in_({r.group_id for r in rows if r.group_id})).all()
    } if rows else {}
    return {
        **ai_wait_status(),
        "total": len(rows),
        "items": [
            {
                "id": r.id, "message_id": r.message_id, "sender": r.sender, "subject": r.subject,
                "template": names.get(r.group_id), "reason": r.note,
                "first_failed_at": r.created_at, "last_tried_at": r.updated_at or r.created_at,
            }
            for r in rows
        ],
    }


@router.post("/failed/rerun")
def rerun_failed(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    """Stop waiting and pull again now - for after the cause has been fixed (credits added).
    Returns at once; the pull itself runs in the background and each mail becomes a job as it
    is read. A mail that fails again is simply recorded again, and the wait starts over."""
    from app.core.email_puller import rerun_failed_emails

    return {"queued": rerun_failed_emails(db)}


class HoldEmailIn(BaseModel):
    message_id: str
    sender: str | None = None
    subject: str | None = None


class HoldEmailsIn(BaseModel):
    items: list[HoldEmailIn]
    note: str = "Not read yet - the AI service was unavailable when it arrived."


@router.post("/failed/hold")
def hold_failed_emails(
    payload: HoldEmailsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    """Put mails on the held list by their Message-ID, so "Rerun all" fetches exactly those
    mails whatever day they arrived. For mails that failed before the held list existed. A
    mail that already has an outcome (a job, a duplicate, set aside) is left alone."""
    from app.core.email_puller import hold_emails

    return hold_emails(db, [i.model_dump() for i in payload.items], payload.note)


class UncountAttemptsIn(BaseModel):
    """Which billed pages to take off: those written between `start` and `end` (ISO date-times,
    with their time zone) against a job that no longer exists. `apply` false only counts."""

    start: datetime
    end: datetime
    tenant_id: str | None = None
    apply: bool = False


@router.post("/failed/uncount-pages")
def uncount_failed_attempt_pages(
    payload: UncountAttemptsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    """Take the pages of failed pull attempts off the bill, for a period in which the puller
    was looping (it billed the pages of every attempt before finding the AI service down, then
    removed the job - see email_puller.AI_DOWN_WAIT_MINUTES). New attempts no longer leave
    these behind; this is for the ones already written.

    A page is taken off only when it was billed inside the period AND its job no longer
    exists. That also matches a job a person really deleted in the same period, which is why
    the period is given explicitly and `apply` defaults to false: look at the count first.
    """
    from app.models.billable_page_event import BillablePageEvent
    from app.models.job import Job

    def _utc_naive(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    start, end = _utc_naive(payload.start), _utc_naive(payload.end)
    query = (
        db.query(BillablePageEvent)
        .filter(BillablePageEvent.created_at >= start, BillablePageEvent.created_at <= end,
                ~BillablePageEvent.job_id.in_(db.query(Job.id)))
    )
    if payload.tenant_id:
        query = query.filter(BillablePageEvent.tenant_id == payload.tenant_id)
    rows = query.all()
    summary = {
        "jobs": len({r.job_id for r in rows}),
        "pages": sum(r.page_count or 0 for r in rows),
        "records": len(rows),
        "applied": False,
    }
    if payload.apply and rows:
        ids = [r.id for r in rows]
        for i in range(0, len(ids), 500):
            db.query(BillablePageEvent).filter(BillablePageEvent.id.in_(ids[i:i + 500])).delete(
                synchronize_session=False)
        db.commit()
        summary["applied"] = True
    return summary
