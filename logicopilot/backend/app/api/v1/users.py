import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import TenantScope, get_current_user, get_db, get_tenant_scope, require_role, scoped_query
from app.core.mail_crypto import encrypt_secret
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User
from app.models.user_type import UserType
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

logger = logging.getLogger(__name__)


def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.mail_connected = bool(user.mail_app_password_encrypted)
    out.modes = user.assigned_modes
    out.user_type_name = user.user_type.name if user.user_type else None
    return out


GMAIL_HOSTS = ["imap.gmail.com"]
# Zoho Mail runs separate regional data centers, each with its own IMAP host - an account
# created in India lives on imap.zoho.in, not the global imap.zoho.com, and a login attempt
# against the wrong region fails with the exact same "Invalid credentials" error a wrong
# password would give, which is actively misleading. Every one is tried in turn.
ZOHO_HOSTS = ["imap.zoho.com", "imap.zoho.in", "imap.zoho.eu", "imap.zoho.com.au", "imap.zoho.jp"]


def _hosts_for(provider: str) -> list[str]:
    return ZOHO_HOSTS if provider == "zoho" else GMAIL_HOSTS


def _normalize_app_password(app_password: str) -> str:
    # Gmail shows the app password in 4 spaced groups; strip them so a copy-paste with
    # spaces still works, and so the SAME normalized value is both what gets verified here
    # and what gets encrypted and stored - not two different strings.
    return app_password.replace(" ", "")


def _try_login(host: str, email: str, app_password: str) -> str | None:
    """Attempt one IMAP login against one host. Returns the failure reason, or None on
    success."""
    import imaplib

    try:
        conn = imaplib.IMAP4_SSL(host)
        try:
            conn.login(email, app_password)
        finally:
            conn.logout()
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{host}: {exc}"


def _verify_mailbox(provider: str, email: str, app_password: str) -> str:
    """Log in to the mailbox right now, before saving anything — a bad app password (or
    the wrong regional host) would otherwise sit silently in the database until the next
    poll failed, hours later, with nobody watching. Tries every known host for this
    provider (matters for Zoho's regional data centers) and returns whichever one actually
    worked, so that exact host - not just a guessed default - is what gets saved. Raises
    HTTPException naming every attempt if none work."""
    failures = []
    for host in _hosts_for(provider):
        failure = _try_login(host, email, app_password)
        if failure is None:
            return host
        failures.append(failure)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(f"Could not sign in to {email} on {provider}: " + "; ".join(failures)
                + ". Check the address, the app password, and that IMAP is enabled."),
    )


def _detect_and_verify_mailbox(email: str, app_password: str) -> tuple[str, str]:
    """No provider given: this IS the app password's only real fingerprint - which host it
    actually logs in to. A custom business domain (ops@4slogistics.com) could be hosted on
    either Google Workspace or Zoho Mail, so there is no way to tell from the address alone;
    trying both (and every Zoho region) is the only reliable answer. Returns
    (provider, host), or raises HTTPException naming every failure if nothing works."""
    failures = []
    for provider in ("gmail", "zoho"):
        for host in _hosts_for(provider):
            failure = _try_login(host, email, app_password)
            if failure is None:
                return provider, host
            failures.append(failure)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(f"Could not sign in to {email} on either Google or Zoho (every region): "
                + "; ".join(failures) + ". Check the address and the app password."),
    )


# An operator used to be REFUSED a template that had no ready ERP script — "You didn't set
# the ERP script for template X. Create its ERP script first."
#
# That guard assumed every template ends in a recorded browser script, and several do not.
# A template whose entry_mode is "fields" or "excel" can be complete and useful with no
# script at all: EXPORT is exactly that, and it meant no operator could be given the work.
#
# Nothing downstream depended on it either. A job whose template has no ready script already
# takes the no_script path in run_erp_script — it records "No ready ERP script is configured
# for this template", marks the job completed, and the operator sees the outcome. Blocking
# the assignment only stopped the work reaching anyone in the first place.


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    creator: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> UserOut:
    """Enforces the creation hierarchy: Super Admin -> Tenant Admin or Admin, Tenant Admin ->
    Operator. Operators are blocked from this endpoint entirely by require_role above."""
    effective_role = payload.role
    user_type: UserType | None = None
    if creator.role == SUPER_ADMIN and payload.role == ADMIN:
        # Cross-tenant, like Super Admin itself — no tenant to belong to, and none is
        # accepted even if one was sent (the check constraint would reject it anyway).
        tenant_id = None
    elif creator.role == SUPER_ADMIN:
        if payload.role != TENANT_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admins may only create tenant_admin or admin accounts here",
            )
        if not payload.tenant_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tenant_id is required")
        tenant = db.get(Tenant, payload.tenant_id)
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant_id = tenant.id
    else:  # TENANT_ADMIN
        if payload.user_type_id:
            user_type = db.get(UserType, payload.user_type_id)
            if user_type is None or user_type.tenant_id != creator.tenant_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That user type does not exist.")
            # The type decides the real role — never trust payload.role once a custom type
            # is given, the same way tenant_id below is never trusted from a non-super-admin.
            effective_role = user_type.base_role
        if effective_role not in (OPERATOR, GK2, MANAGER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant admins may only create operator, GK2, or manager accounts",
            )
        if effective_role == GK2 and not payload.modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pick at least one shipment mode for this GK2 user to review.",
            )
        if effective_role == MANAGER and payload.modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manager accounts do not use shipment modes — they see every job in the tenant.",
            )
        if effective_role == MANAGER and payload.template_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manager accounts do not use template assignments.",
            )
        # Force the creator's own tenant regardless of what the caller passed — never trust
        # a tenant_id supplied by a non-super-admin caller.
        tenant_id = creator.tenant_id

    # Checked BEFORE the insert, and case-insensitively, so the answer names the account in
    # the way. The old code let the database raise and reported every IntegrityError as
    # "a user with this email already exists" — which is a guess: a bad tenant id or a
    # duplicate template assignment produced exactly the same sentence, and an operator
    # deleting a user and being told the email was still taken had no way to tell whether it
    # really was. Login matches on lowercase, so two rows differing only in case would also
    # leave one of them permanently unreachable.
    clash = db.query(User).filter(func.lower(User.email) == payload.email.lower()).first()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"{clash.email} is already registered as a "
                    f"{clash.role.replace('_', ' ')}. Delete that account first, or use "
                    "another address."),
        )

    # Provider is optional - given, it is trusted and verified directly; omitted, it is
    # detected by trying both hosts with the same credentials (see _detect_and_verify_mailbox).
    if bool(payload.mail_email) != bool(payload.mail_app_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email and app password are both required to connect a mailbox.",
        )
    detected_provider = None
    detected_host = None
    mail_app_password = None
    if payload.mail_email and payload.mail_app_password:
        mail_app_password = _normalize_app_password(payload.mail_app_password)
        if payload.mail_provider:
            detected_host = _verify_mailbox(payload.mail_provider, payload.mail_email, mail_app_password)
            detected_provider = payload.mail_provider
        else:
            detected_provider, detected_host = _detect_and_verify_mailbox(payload.mail_email, mail_app_password)

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=effective_role,
        tenant_id=tenant_id,
        created_by_id=creator.id,
        user_type_id=user_type.id if user_type else None,
        assigned_modes=sorted(set(payload.modes)) if effective_role in (GK2, OPERATOR) and payload.modes else None,
    )
    if detected_provider:
        user.mail_provider = detected_provider
        user.mail_host = detected_host
        user.mail_email = payload.mail_email
        user.mail_app_password_encrypted = encrypt_secret(mail_app_password)
    db.add(user)
    try:
        db.flush()  # assign user.id before creating assignments, all in one transaction
        if payload.template_ids:
            from app.models.template_group import TemplateGroup
            from app.models.user_template import UserTemplateAssignment

            for gid in dict.fromkeys(payload.template_ids):  # de-dupe, keep order
                group = db.get(TemplateGroup, gid)
                if group is None or group.tenant_id != tenant_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A selected template does not belong to this tenant.",
                    )
                db.add(UserTemplateAssignment(tenant_id=tenant_id, user_id=user.id, group_id=gid))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The duplicate email is already ruled out above, so anything landing here is a
        # DIFFERENT constraint. Say what the database actually said rather than repeating a
        # guess about the email — that is what made this impossible to diagnose from the
        # screen.
        logger.exception("could not create user %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not create this user: {str(getattr(exc, 'orig', exc))[:200]}",
        )
    db.refresh(user)
    return _user_out(user)


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), scope: TenantScope = Depends(get_tenant_scope)) -> list[UserOut]:
    users = scoped_query(db, User, scope).all()
    return [_user_out(u) for u in users]


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str, db: Session = Depends(get_db), scope: TenantScope = Depends(get_tenant_scope)) -> UserOut:
    user = scoped_query(db, User, scope).filter(User.id == user_id).first()
    if user is None:
        # 404, not 403: don't confirm to a caller that a user in another tenant exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    actor: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> UserOut:
    """Mirrors the creation hierarchy: Super Admin manages tenant_admin accounts,
    Tenant Admin manages operator accounts within their own tenant (enforced by scoped_query)."""
    user = scoped_query(db, User, scope).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == actor.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot change your own account")

    if actor.role == SUPER_ADMIN and user.role not in (TENANT_ADMIN, ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admins may only manage tenant_admin or admin accounts here",
        )
    if actor.role == TENANT_ADMIN and user.role not in (OPERATOR, GK2, MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant admins may only manage operator, GK2, or manager accounts")

    if payload.email is not None:
        new_email = payload.email.strip()
        if new_email.lower() != user.email.lower():
            clash = (
                db.query(User)
                .filter(func.lower(User.email) == new_email.lower(), User.id != user.id)
                .first()
            )
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists"
                )
            user.email = new_email
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.password is not None:
        user.hashed_password = hash_password(payload.password)
    if payload.is_active is not None:
        user.is_active = payload.is_active

    if payload.mail_app_password is not None:
        if payload.mail_app_password == "":
            # Explicit clear: disconnect the mailbox entirely.
            user.mail_provider = None
            user.mail_host = None
            user.mail_email = None
            user.mail_app_password_encrypted = None
        else:
            email = payload.mail_email or user.mail_email
            if not email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An email address is required to connect a mailbox.",
                )
            normalized = _normalize_app_password(payload.mail_app_password)
            provider = payload.mail_provider or user.mail_provider
            if provider:
                host = _verify_mailbox(provider, email, normalized)
            else:
                provider, host = _detect_and_verify_mailbox(email, normalized)
            user.mail_provider = provider
            user.mail_host = host
            user.mail_email = email
            user.mail_app_password_encrypted = encrypt_secret(normalized)
    elif payload.mail_provider is not None or payload.mail_email is not None:
        # Provider/email changed with no new password - re-verify against the existing one
        # rather than silently trusting an edited address the stored password was never
        # checked against.
        if not user.mail_app_password_encrypted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Set an app password to connect a mailbox.",
            )
        from app.core.mail_crypto import decrypt_secret

        provider = payload.mail_provider or user.mail_provider
        email = payload.mail_email or user.mail_email
        host = _verify_mailbox(provider, email, decrypt_secret(user.mail_app_password_encrypted))
        user.mail_provider = provider
        user.mail_host = host
        user.mail_email = email

    if payload.modes is not None and user.role in (GK2, OPERATOR):
        if user.role == GK2 and not payload.modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A GK2 user needs at least one shipment mode to review.",
            )
        user.assigned_modes = sorted(set(payload.modes)) if payload.modes else None
    elif payload.modes is not None and user.role == MANAGER and payload.modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manager accounts do not use shipment modes — they see every job in the tenant.",
        )

    if payload.template_ids is not None and user.role == MANAGER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Manager accounts do not use template assignments.",
        )

    if payload.template_ids is not None:
        from app.models.template_group import TemplateGroup
        from app.models.user_template import UserTemplateAssignment

        # Replace the user's assignments with the provided set.
        db.query(UserTemplateAssignment).filter(UserTemplateAssignment.user_id == user.id).delete()
        for gid in dict.fromkeys(payload.template_ids):
            group = db.get(TemplateGroup, gid)
            if group is None or group.tenant_id != user.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A selected template does not belong to this user's tenant.",
                )
            db.add(UserTemplateAssignment(tenant_id=user.tenant_id, user_id=user.id, group_id=gid))

    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    actor: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> None:
    """Delete a user and everything tied to them. Super Admin may delete tenant_admins
    or operators; a Tenant Admin may delete operators in their own tenant. Deleting a
    tenant_admin also removes the operators they created. All related jobs, job docs,
    field values, template assignments, and sessions are cleaned up."""
    from sqlalchemy import text

    user = scoped_query(db, User, scope).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == actor.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account")
    if actor.role == SUPER_ADMIN and user.role not in (TENANT_ADMIN, OPERATOR, ADMIN, GK2, MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this account")
    if actor.role == TENANT_ADMIN and user.role not in (OPERATOR, GK2, MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant admins may only delete operator, GK2, or manager accounts")

    # The user + (if a tenant admin) the operators, GK2, and manager users they created.
    ids = [user.id]
    if user.role == TENANT_ADMIN:
        ids += [
            o.id for o in db.query(User)
            .filter(User.created_by_id == user.id, User.role.in_((OPERATOR, GK2, MANAGER))).all()
        ]

    for uid in ids:
        db.execute(text("DELETE FROM job_field_values WHERE job_id IN (SELECT id FROM jobs WHERE created_by_id=:u)"), {"u": uid})
        db.execute(text("DELETE FROM job_documents WHERE job_id IN (SELECT id FROM jobs WHERE created_by_id=:u)"), {"u": uid})
        db.execute(text("DELETE FROM jobs WHERE created_by_id=:u"), {"u": uid})
        db.execute(text("DELETE FROM user_template_assignments WHERE user_id=:u"), {"u": uid})
        db.execute(text("DELETE FROM refresh_tokens WHERE user_id=:u"), {"u": uid})
    for uid in ids:
        u = db.get(User, uid)
        if u is not None:
            db.delete(u)
    db.commit()


@router.get("/{user_id}/templates")
def get_user_templates(
    user_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    _=Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> dict:
    """Template ids currently assigned to a user (for pre-filling the edit form)."""
    from app.models.user_template import UserTemplateAssignment

    user = scoped_query(db, User, scope).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    rows = db.query(UserTemplateAssignment).filter(UserTemplateAssignment.user_id == user_id).all()
    return {"template_ids": [r.group_id for r in rows]}
