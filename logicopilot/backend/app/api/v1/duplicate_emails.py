"""Pre-alerts that announce a BL this customer already sent us, and what to do with them.

Held rather than acted on: the mail is kept whole and nothing is read out of it, so the
decision stays with a person. Two ways out, and they are the only two -

    Delete              it really was a re-send; drop it and the copy of the message with it
    Process as new job  it really is a separate shipment; run it exactly as if it had just
                        arrived, through the same pipeline, from the beginning

See app/core/duplicate_bl.py for why this never becomes a job on its own.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_tenant_scope, require_role
from app.models.duplicate_email import DuplicateEmail
from app.models.template_group import TemplateGroup
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/duplicate-emails", tags=["duplicate-emails"])


class DuplicateEmailOut(BaseModel):
    id: str
    message_id: str
    sender: str | None = None
    customer: str | None = None
    subject: str | None = None
    received_at: datetime | None = None
    bl_number: str
    original_job_id: str | None = None
    original_reference: str | None = None
    original_received_at: datetime | None = None
    status: str
    can_process: bool


def _out(row: DuplicateEmail, customer: str | None) -> DuplicateEmailOut:
    return DuplicateEmailOut(
        id=row.id, message_id=row.message_id, sender=row.sender, customer=customer,
        subject=row.subject, received_at=row.received_at, bl_number=row.bl_number,
        original_job_id=row.original_job_id, original_reference=row.original_reference,
        original_received_at=row.original_received_at, status=row.status,
        # Without the kept message there is nothing to replay, so the button would lie.
        can_process=bool(row.raw_path and Path(row.raw_path).exists()),
    )


@router.get("", response_model=list[DuplicateEmailOut])
def list_duplicate_emails(
    bl: str | None = Query(None, description="filter by BL number, any spelling"),
    db: Session = Depends(get_db),
    scope=Depends(get_tenant_scope),
    _: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> list[DuplicateEmailOut]:
    """Everything still waiting on a decision. Handled rows stay in the table (so the same
    message arriving again is recognised rather than queued twice) but never show here."""
    from app.core.email_bl import normalise_bl

    q = db.query(DuplicateEmail).filter(DuplicateEmail.status == "pending")
    tenant_id = getattr(scope, "tenant_id", None)
    if tenant_id:
        q = q.filter(DuplicateEmail.tenant_id == tenant_id)
    if bl and bl.strip():
        q = q.filter(DuplicateEmail.bl_number.like(f"%{normalise_bl(bl)}%"))
    rows = q.order_by(DuplicateEmail.received_at.desc()).all()

    names = {g.id: g.name for g in db.query(TemplateGroup).all()}
    return [_out(r, names.get(r.group_id)) for r in rows]


@router.delete("/{duplicate_id}", response_model=DuplicateEmailOut)
def delete_duplicate_email(
    duplicate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN)),
) -> DuplicateEmailOut:
    """It really was a re-send. The row stays (so the same message arriving again is still
    recognised) but the kept copy of the mail goes - it is a full copy with every
    attachment, and nothing refers to it once this decision is made."""
    from app.core.duplicate_bl import discard_raw

    row = db.get(DuplicateEmail, duplicate_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    discard_raw(row)
    row.raw_path = None
    row.status = "deleted"
    row.handled_by_id = user.id
    row.handled_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("duplicate %s (BL %s) deleted by %s", row.id, row.bl_number, user.email)
    names = {g.id: g.name for g in db.query(TemplateGroup).all()}
    return _out(row, names.get(row.group_id))


@router.post("/{duplicate_id}/process", response_model=DuplicateEmailOut)
def process_duplicate_email(
    duplicate_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN)),
) -> DuplicateEmailOut:
    """It really is a separate shipment. Run the kept message through the ORDINARY pipeline -
    the same classification, the same extraction, the same everything a mail that had never
    looked like a duplicate would get.

    Deliberately replays the real pipeline rather than reimplementing it: that is the one
    place this product decides what a message means, and a second copy of that decision
    would drift from it. `reexamine=True` is what lets the message be reopened and what
    tells the duplicate check to stand aside - a person has already judged this one.
    """
    from app.core.custom_page_filter import get_active_custom_filter_texts
    from app.core.duplicate_bl import discard_raw
    from app.core.email_puller import _mailbox_setup, _process_one_message
    from app.core.config import get_settings
    from app.core.system_settings import get_extraction_engine

    row = db.get(DuplicateEmail, duplicate_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not row.raw_path or not Path(row.raw_path).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The copy of this email is no longer on disk, so it cannot be processed. "
                   "Ask the sender to forward it again.",
        )

    ctx, early = _mailbox_setup(db, row.tenant_id, row.group_id)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(early or {}).get("note") or "No customer is configured for this email.",
        )

    extraction_engine = get_extraction_engine(db)
    vision_engine_model = get_settings().vision_engine_model
    custom_filter_texts = get_active_custom_filter_texts(db)
    result = _process_one_message(
        db, b"0", Path(row.raw_path).read_bytes(), ctx,
        None, None, None,
        extraction_engine, vision_engine_model, custom_filter_texts,
        reexamine=True,
    ) or {}

    job_id = result.get("job_id")
    if not job_id:
        # Nothing was created - the mail had no usable documents, or no customer could be
        # identified. Leave it pending: the operator can look at why and try again or delete
        # it, which is better than quietly marking it done with nothing to show.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.get("reason") or "No job could be created from this email.",
        )

    row.status = "processed"
    row.processed_job_id = job_id
    row.handled_by_id = user.id
    row.handled_at = datetime.now(timezone.utc)
    discard_raw(row)
    row.raw_path = None
    db.commit()
    logger.info("duplicate %s (BL %s) processed into job %s by %s",
                row.id, row.bl_number, job_id, user.email)
    names = {g.id: g.name for g in db.query(TemplateGroup).all()}
    return _out(row, names.get(row.group_id))
