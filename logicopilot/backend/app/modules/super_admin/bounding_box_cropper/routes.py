import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.docai import get_page_ocr
from app.core.llm import generate_field_prompt

from . import schemas
from .models import FieldMapping
from .services import detect_value_and_anchor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Super Admin / Bounding Box Cropper"])

VALID_COLORS = {"red", "green", "blue", "yellow", "purple"}


def _template_row(db: Session, template_id: int) -> dict:
    row = db.execute(
        text("SELECT id, tenant_id, page_count FROM templates WHERE id = :tid"),
        {"tid": template_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return dict(row)


def _label_row(db: Session, label_id: int) -> dict:
    row = db.execute(
        text("SELECT id, tenant_id, field_name FROM labels WHERE id = :lid"),
        {"lid": label_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Label not found")
    return dict(row)


def _validate_payload(payload: schemas.FieldMappingCreate, template: dict) -> None:
    if payload.color not in VALID_COLORS:
        raise HTTPException(
            status_code=422, detail=f"color must be one of {sorted(VALID_COLORS)}"
        )
    if payload.page_number < 1 or payload.page_number > template["page_count"]:
        raise HTTPException(
            status_code=422,
            detail=f"page_number out of range (template has {template['page_count']} page(s)).",
        )
    for name in ("x", "y", "width", "height"):
        value = getattr(payload, name)
        if not 0 <= value <= 1:
            raise HTTPException(
                status_code=422,
                detail=f"{name} must be a normalized 0-1 coordinate (got {value}).",
            )
    if payload.width <= 0.001 or payload.height <= 0.001:
        raise HTTPException(status_code=422, detail="Crop box is too small.")
    if payload.x + payload.width > 1.001 or payload.y + payload.height > 1.001:
        raise HTTPException(status_code=422, detail="Crop box extends outside the page.")


def _run_anchor_detection(mapping: FieldMapping, field_name: str, db: Session) -> str | None:
    """Best effort: OCR the page (cached), find value + anchor, generate the prompt.
    Returns an error string instead of raising, so a crop is never lost because
    OCR was unavailable — the UI can offer a re-detect."""
    template_dir = settings.UPLOADS_DIR / "templates" / str(mapping.template_id)
    try:
        ocr = get_page_ocr(template_dir, mapping.page_number)
    except Exception as exc:  # noqa: BLE001 — network/credential/API failures land here
        logger.warning("Document AI OCR failed for template %s page %s: %s",
                       mapping.template_id, mapping.page_number, exc)
        return f"Document AI OCR failed: {exc}"

    box = {
        "x0": mapping.x,
        "y0": mapping.y,
        "x1": mapping.x + mapping.width,
        "y1": mapping.y + mapping.height,
    }
    value_text, anchor_term = detect_value_and_anchor(ocr, box)
    mapping.anchor_term = anchor_term
    mapping.generated_prompt = generate_field_prompt(
        field_name, anchor_term, value_text, mapping.page_number
    )
    db.commit()
    db.refresh(mapping)
    return None


@router.post(
    "/templates/{template_id}/field-mappings",
    response_model=schemas.FieldMappingRead,
    status_code=status.HTTP_201_CREATED,
)
def create_field_mapping(
    template_id: int, payload: schemas.FieldMappingCreate, db: Session = Depends(get_db)
):
    template = _template_row(db, template_id)
    label = _label_row(db, payload.label_id)
    if label["tenant_id"] != template["tenant_id"]:
        raise HTTPException(
            status_code=422, detail="Label belongs to a different tenant than the template."
        )
    _validate_payload(payload, template)

    mapping = FieldMapping(
        template_id=template_id,
        label_id=payload.label_id,
        page_number=payload.page_number,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        color=payload.color,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    # Anchor detection is best-effort: the crop is already saved; a Document AI
    # outage just leaves anchor_term null for a later re-detect.
    _run_anchor_detection(mapping, label["field_name"], db)
    return mapping


@router.get(
    "/templates/{template_id}/field-mappings",
    response_model=list[schemas.FieldMappingRead],
)
def list_field_mappings(template_id: int, db: Session = Depends(get_db)):
    _template_row(db, template_id)
    return (
        db.query(FieldMapping)
        .filter(FieldMapping.template_id == template_id)
        .order_by(FieldMapping.id)
        .all()
    )


@router.patch("/field-mappings/{field_mapping_id}", response_model=schemas.FieldMappingRead)
def update_field_mapping(
    field_mapping_id: int, payload: schemas.FieldMappingCreate, db: Session = Depends(get_db)
):
    mapping = db.get(FieldMapping, field_mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Field mapping not found")
    template = _template_row(db, mapping.template_id)
    label = _label_row(db, payload.label_id)
    if label["tenant_id"] != template["tenant_id"]:
        raise HTTPException(
            status_code=422, detail="Label belongs to a different tenant than the template."
        )
    _validate_payload(payload, template)

    mapping.label_id = payload.label_id
    mapping.page_number = payload.page_number
    mapping.x = payload.x
    mapping.y = payload.y
    mapping.width = payload.width
    mapping.height = payload.height
    mapping.color = payload.color
    db.commit()
    db.refresh(mapping)

    _run_anchor_detection(mapping, label["field_name"], db)
    return mapping


@router.delete("/field-mappings/{field_mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field_mapping(field_mapping_id: int, db: Session = Depends(get_db)):
    mapping = db.get(FieldMapping, field_mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Field mapping not found")
    db.delete(mapping)
    db.commit()


@router.post(
    "/field-mappings/{field_mapping_id}/detect-anchor",
    response_model=schemas.FieldMappingRead,
)
def detect_anchor_term(field_mapping_id: int, db: Session = Depends(get_db)):
    """Re-runs Document AI anchor detection + prompt generation for one crop."""
    mapping = db.get(FieldMapping, field_mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Field mapping not found")
    label = _label_row(db, mapping.label_id)

    error = _run_anchor_detection(mapping, label["field_name"], db)
    if error:
        raise HTTPException(status_code=502, detail=error)
    return mapping
