"""Connect, assign and manage mailboxes for the email puller - independent of any operator
user (see app/models/connected_mailbox.py's own docstring for why). Super Admin only: this
is a platform-level action, not a tenant's own, the same reasoning template-tenant
reassignment uses.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.mail_crypto import encrypt_secret
from app.models.connected_mailbox import ConnectedMailbox
from app.models.user import SUPER_ADMIN, User
from app.schemas.connected_mailbox import ConnectedMailboxOut, ConnectMailboxIn, UpdateMailboxIn

router = APIRouter(prefix="/email-puller/mailboxes", tags=["email puller mailboxes"])


def _out(mb: ConnectedMailbox, tenant_name: str | None, group_name: str | None) -> ConnectedMailboxOut:
    out = ConnectedMailboxOut.model_validate(mb)
    out.tenant_name = tenant_name
    out.group_name = group_name
    return out


def _with_names(db: Session, mb: ConnectedMailbox) -> ConnectedMailboxOut:
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    tenant_name = db.get(Tenant, mb.tenant_id).name if mb.tenant_id else None
    group = db.get(TemplateGroup, mb.group_id) if mb.group_id else None
    return _out(mb, tenant_name, group.name if group else None)


@router.get("", response_model=list[ConnectedMailboxOut])
def list_mailboxes(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[ConnectedMailboxOut]:
    """Every connected mailbox, any tenant, any assignment state - including Unassigned
    (tenant_id is null) ones, which the puller itself always skips (see email_puller.py)."""
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    rows = db.query(ConnectedMailbox).order_by(ConnectedMailbox.created_at.desc()).all()
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    groups = {g.id: g.name for g in db.query(TemplateGroup).all()}
    return [
        _out(mb, tenants.get(mb.tenant_id) if mb.tenant_id else None,
             groups.get(mb.group_id) if mb.group_id else None)
        for mb in rows
    ]


@router.post("", response_model=ConnectedMailboxOut, status_code=status.HTTP_201_CREATED)
def connect_mailbox(
    payload: ConnectMailboxIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> ConnectedMailboxOut:
    """Verify the mailbox is actually reachable right now (same live IMAP login
    users.py's own mailbox-connect uses), then save it with NO tenant/template - it shows up
    Unassigned until a Super Admin picks both from the list."""
    from app.api.v1.users import _detect_and_verify_mailbox, _normalize_app_password, _verify_mailbox

    existing = db.query(ConnectedMailbox).filter(
        ConnectedMailbox.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{payload.email} is already connected.")

    app_password = _normalize_app_password(payload.app_password)
    if payload.provider:
        host = _verify_mailbox(payload.provider, payload.email, app_password)
        provider = payload.provider
    else:
        provider, host = _detect_and_verify_mailbox(payload.email, app_password)

    mb = ConnectedMailbox(
        provider=provider, email=payload.email, host=host,
        app_password_encrypted=encrypt_secret(app_password),
    )
    db.add(mb)
    db.commit()
    db.refresh(mb)
    return _with_names(db, mb)


@router.patch("/{mailbox_id}", response_model=ConnectedMailboxOut)
def update_mailbox(
    mailbox_id: str,
    payload: UpdateMailboxIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> ConnectedMailboxOut:
    """Assign (or change) the tenant and/or template, or toggle paused. Changing tenant_id
    to a genuinely different tenant clears group_id - a template belongs to ONE tenant, so an
    old group assignment from before the change can never still be valid."""
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    mb = db.get(ConnectedMailbox, mailbox_id)
    if mb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")

    updates = payload.model_dump(exclude_unset=True)

    if "tenant_id" in updates:
        new_tenant_id = updates["tenant_id"]
        if new_tenant_id is not None and db.get(Tenant, new_tenant_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if new_tenant_id != mb.tenant_id:
            mb.group_id = None  # an old group_id never survives a real tenant change
        mb.tenant_id = new_tenant_id

    if "group_id" in updates:
        new_group_id = updates["group_id"]
        if new_group_id is not None:
            group = db.get(TemplateGroup, new_group_id)
            if group is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
            effective_tenant = updates.get("tenant_id", mb.tenant_id)
            if group.tenant_id != effective_tenant:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="That template does not belong to this mailbox's tenant.",
                )
        mb.group_id = new_group_id

    if "paused" in updates:
        mb.paused = bool(updates["paused"])

    db.commit()
    db.refresh(mb)
    return _with_names(db, mb)


@router.delete("/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_mailbox(
    mailbox_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> None:
    mb = db.get(ConnectedMailbox, mailbox_id)
    if mb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mailbox not found")
    db.delete(mb)
    db.commit()
