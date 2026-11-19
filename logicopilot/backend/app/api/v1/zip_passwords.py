"""Tenant Admin -> "Zip passwords": the passwords a customer's password-protected zips use, tried
automatically on every locked zip in this tenant's mail and uploads (app/core/zip_passwords.py).

A password is write-only: it is never returned after it is saved - the list shows the label,
where it applies, and how often it has opened a zip."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.zip_passwords import save_password
from app.models.archive_password import ArchivePassword
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(prefix="/zip-passwords", tags=["zip-passwords"])


class ZipPasswordOut(BaseModel):
    id: str
    label: str
    group_id: str | None = None
    group_name: str | None = None
    sender_domains: list[str] | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
    created_at: datetime | None = None


class ZipPasswordIn(BaseModel):
    label: str
    password: str
    group_id: str | None = None
    sender_domains: list[str] | None = None


def _tenant_of(user: User, tenant_id: str | None) -> str:
    if user.role == SUPER_ADMIN:
        if not tenant_id:
            raise HTTPException(status_code=422, detail="tenant_id is required for a Super Admin.")
        return tenant_id
    return user.tenant_id


def _out(db: Session, r: ArchivePassword) -> ZipPasswordOut:
    group = db.get(TemplateGroup, r.group_id) if r.group_id else None
    return ZipPasswordOut(id=r.id, label=r.label, group_id=r.group_id, group_name=group.name if group else None,
                          sender_domains=r.sender_domains, last_used_at=r.last_used_at,
                          use_count=r.use_count or 0, created_at=r.created_at)


@router.get("", response_model=list[ZipPasswordOut])
def list_zip_passwords(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN, SUPER_ADMIN)),
) -> list[ZipPasswordOut]:
    tid = _tenant_of(user, tenant_id)
    rows = (db.query(ArchivePassword).filter(ArchivePassword.tenant_id == tid)
            .order_by(ArchivePassword.label, ArchivePassword.created_at).all())
    return [_out(db, r) for r in rows]


@router.post("", response_model=ZipPasswordOut, status_code=status.HTTP_201_CREATED)
def add_zip_password(
    payload: ZipPasswordIn,
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN, SUPER_ADMIN)),
) -> ZipPasswordOut:
    tid = _tenant_of(user, tenant_id)
    if not payload.password.strip():
        raise HTTPException(status_code=422, detail="Enter the password.")
    if not payload.label.strip():
        raise HTTPException(status_code=422, detail="Enter a label, e.g. the customer's name.")
    if payload.group_id:
        group = db.get(TemplateGroup, payload.group_id)
        if group is None or group.tenant_id != tid:
            raise HTTPException(status_code=404, detail="Template not found")
    row = save_password(db, tenant_id=tid, label=payload.label, password=payload.password.strip(),
                        group_id=payload.group_id, sender_domains=payload.sender_domains,
                        created_by_id=user.id)
    db.commit()
    db.refresh(row)
    return _out(db, row)


@router.delete("/{password_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zip_password(
    password_id: str,
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN, SUPER_ADMIN)),
) -> None:
    tid = _tenant_of(user, tenant_id)
    row = db.get(ArchivePassword, password_id)
    if row is None or row.tenant_id != tid:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
