from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

from . import schemas
from .models import Label

router = APIRouter(tags=["Super Admin / Label Builder"])

VALID_DATA_TYPES = {"String", "Number", "Date", "Currency"}


def _validate_data_type(data_type: str) -> None:
    if data_type not in VALID_DATA_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"data_type must be one of {sorted(VALID_DATA_TYPES)}",
        )


@router.post("/tenants/{tenant_id}/labels", response_model=schemas.LabelRead, status_code=status.HTTP_201_CREATED)
def create_label(tenant_id: int, payload: schemas.LabelCreate, db: Session = Depends(get_db)):
    _validate_data_type(payload.data_type)
    tenant_exists = db.execute(
        text("SELECT 1 FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    ).scalar()
    if not tenant_exists:
        raise HTTPException(status_code=404, detail="Tenant not found")

    duplicate = (
        db.query(Label)
        .filter(Label.tenant_id == tenant_id, Label.field_name == payload.field_name)
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail=f'A label named "{payload.field_name}" already exists for this tenant.',
        )

    label = Label(tenant_id=tenant_id, **payload.model_dump())
    db.add(label)
    db.commit()
    db.refresh(label)
    return label


@router.get("/tenants/{tenant_id}/labels", response_model=list[schemas.LabelRead])
def list_labels(tenant_id: int, db: Session = Depends(get_db)):
    return db.query(Label).filter(Label.tenant_id == tenant_id).order_by(Label.id).all()


@router.patch("/labels/{label_id}", response_model=schemas.LabelRead)
def update_label(label_id: int, payload: schemas.LabelUpdate, db: Session = Depends(get_db)):
    label = db.get(Label, label_id)
    if label is None:
        raise HTTPException(status_code=404, detail="Label not found")
    updates = payload.model_dump(exclude_unset=True)
    if "data_type" in updates:
        _validate_data_type(updates["data_type"])
    for field, value in updates.items():
        setattr(label, field, value)
    db.commit()
    db.refresh(label)
    return label


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(label_id: int, db: Session = Depends(get_db)):
    label = db.get(Label, label_id)
    if label is None:
        raise HTTPException(status_code=404, detail="Label not found")
    # Raw-SQL count keeps this feature folder decoupled from the cropper's model class.
    mappings = db.execute(
        text("SELECT COUNT(*) FROM field_mappings WHERE label_id = :lid"), {"lid": label_id}
    ).scalar()
    if mappings:
        raise HTTPException(
            status_code=409,
            detail=f"Label is used by {mappings} field mapping(s) — delete those crops first.",
        )
    db.delete(label)
    db.commit()
