from fastapi import APIRouter, Depends
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
    - Tenant Admin: their tenant's customers.
    - Operator: only customers routed to them (their own mail).

    `reexamine=true` looks again at messages already examined and set aside. Use it after
    configuring a customer who had none: their earlier mail was deliberately left unread for
    exactly this, and is otherwise skipped so the same OCR and model calls are not paid twice.
    """
    if user.role == SUPER_ADMIN:
        return pull_inbox(db, reexamine=reexamine)
    if user.role == TENANT_ADMIN:
        return pull_inbox(db, tenant_id=user.tenant_id, reexamine=reexamine)
    return pull_inbox(db, tenant_id=user.tenant_id, operator_id=user.id, reexamine=reexamine)
