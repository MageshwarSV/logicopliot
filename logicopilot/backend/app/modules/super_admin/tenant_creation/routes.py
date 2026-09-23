from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db

from . import schemas
from .models import Tenant

router = APIRouter(prefix="/tenants", tags=["Super Admin / Tenant Creation"])


@router.post("", response_model=schemas.TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: schemas.TenantCreate, db: Session = Depends(get_db)):
    tenant = Tenant(**payload.model_dump())
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("", response_model=list[schemas.TenantRead])
def list_tenants(db: Session = Depends(get_db)):
    return db.query(Tenant).order_by(Tenant.created_at.desc()).all()


@router.get("/{tenant_id}", response_model=schemas.TenantRead)
def get_tenant(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/{tenant_id}", response_model=schemas.TenantRead)
def update_tenant(tenant_id: int, payload: schemas.TenantUpdate, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)
    db.commit()
    db.refresh(tenant)
    return tenant
