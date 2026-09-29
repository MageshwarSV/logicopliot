import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_role
from app.core.modes import MODES
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import ADMIN, GK2, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.tenant import TenantCreate, TenantOut, TenantUpdate

WRITE_TOGGLE_ROLES = (OPERATOR, GK2)

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    creator: User = Depends(require_role(SUPER_ADMIN)),
) -> TenantOut:
    """Creates the company and, if admin credentials are supplied, its tenant-admin
    login in the same step (atomic — a bad email rolls the whole thing back)."""
    wants_admin = any([payload.admin_full_name, payload.admin_email, payload.admin_password])
    if wants_admin and not all([payload.admin_full_name, payload.admin_email, payload.admin_password]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide admin name, email, and password together (or none).",
        )
    if wants_admin and len(payload.admin_password or "") < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admin password must be at least 8 characters.")

    allowed_modes = None
    if payload.allowed_modes:
        allowed_modes = sorted(set(payload.allowed_modes))
        bad = [m for m in allowed_modes if m not in MODES]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a valid mode: {', '.join(bad)}",
            )

    tenant = Tenant(name=payload.name, region=payload.region, currency=payload.currency,
                    allowed_modes=allowed_modes)
    db.add(tenant)
    try:
        db.flush()  # assign tenant.id; still one transaction with the admin below
        if wants_admin:
            db.add(
                User(
                    email=payload.admin_email,
                    hashed_password=hash_password(payload.admin_password),
                    full_name=payload.admin_full_name,
                    role=TENANT_ADMIN,
                    tenant_id=tenant.id,
                    created_by_id=creator.id,
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A tenant with this name, or a user with that admin email, already exists.",
        )
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.get("", response_model=list[TenantOut])
def list_tenants(
    db: Session = Depends(get_db),
    # ADMIN needs this too: the jobs list's tenant-filter dropdown is built from it.
    _=Depends(require_role(SUPER_ADMIN, ADMIN)),
) -> list[TenantOut]:
    tenants = db.query(Tenant).all()
    return [TenantOut.model_validate(t) for t in tenants]


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantOut:
    """Super admins can look up any tenant; everyone else only their own (404 otherwise) —
    this is how a Tenant Admin/Operator dashboard shows its own tenant's name."""
    if user.role != SUPER_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return TenantOut.model_validate(tenant)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantOut:
    """Super Admin may edit any tenant; a Tenant Admin only their own (404 otherwise, same
    as get_tenant) — this is how the Tenant Admin's own "Shipment Type" page manages which
    modes THEIR tenant is licensed for, without needing Super Admin access."""
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if payload.allowed_modes is not None:
        allowed_modes = sorted(set(payload.allowed_modes)) if payload.allowed_modes else None
        bad = [m for m in (allowed_modes or []) if m not in MODES]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a valid mode: {', '.join(bad)}",
            )
        tenant.allowed_modes = allowed_modes

    if payload.role_write_enabled is not None:
        bad_roles = [r for r in payload.role_write_enabled if r not in WRITE_TOGGLE_ROLES]
        if bad_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Not a role this can be set for: {', '.join(bad_roles)}",
            )
        # Merge, not replace — the Masters page edits one role type at a time and must not
        # silently reset the other one's setting.
        merged = dict(tenant.role_write_enabled or {})
        merged.update(payload.role_write_enabled)
        tenant.role_write_enabled = merged

    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.post("/{tenant_id}/irn-pending-key", response_model=TenantOut)
def generate_irn_pending_key(
    tenant_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TenantOut:
    """The Super Admin dashboard's "Generate Link" button, for the standalone, no-login IRN
    Pending page (see app/api/v1/public_irn.py) - a tenant's key is created once, the first
    time this is called for them, and never rotated automatically after that, so a link
    already handed out keeps working. Calling this again for a tenant that already has a key
    is a no-op that just returns it - pressing the button again means "show me the link", not
    "invalidate whatever copy is already in use"."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if not tenant.irn_pending_access_key:
        tenant.irn_pending_access_key = secrets.token_urlsafe(32)
        db.commit()
        db.refresh(tenant)
    return TenantOut.model_validate(tenant)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    """Delete a tenant and everything scoped to it: its admins + operators, templates
    (documents, marks, cross-doc links, reviews), ERP scripts, jobs (docs + field values),
    assignments, access requests, and the sessions of its users. Super Admin only."""
    from sqlalchemy import text

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    params = {"t": tenant_id}
    # Sessions of this tenant's users first (no tenant_id column of their own).
    db.execute(
        text("DELETE FROM refresh_tokens WHERE user_id IN (SELECT id FROM users WHERE tenant_id=:t)"),
        params,
    )
    # Everything else is tenant-scoped; children before parents.
    for tbl in (
        "job_field_values",
        "job_documents",
        "jobs",
        "cross_doc_links",
        "field_marks",
        "template_documents",
        "template_reviews",
        "user_template_assignments",
        "erp_scripts",
        "erp_access_requests",
        "custom_fields",
        "job_failure_reports",
        "template_groups",
        "users",
    ):
        db.execute(text(f"DELETE FROM {tbl} WHERE tenant_id=:t"), params)
    db.delete(tenant)
    db.commit()
