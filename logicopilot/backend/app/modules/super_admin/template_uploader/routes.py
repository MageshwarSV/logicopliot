import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db

from . import schemas
from .models import Template

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Super Admin / Template Uploader"])

VALID_DOCUMENT_TYPES = {"Invoice", "PackingList", "BL", "Custom"}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
RENDER_DPI = 150


def template_dir(template_id: int) -> Path:
    return settings.UPLOADS_DIR / "templates" / str(template_id)


def _render_pages(original: Path, pages_dir: Path) -> int:
    """Render every page of a PDF (or a single image) to PNG previews.
    Returns the page count. PNG output keeps one uniform format for the
    preview endpoint and for Document AI OCR."""
    import fitz  # PyMuPDF

    pages_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(original) as doc:
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=RENDER_DPI)
            pix.save(pages_dir / f"page_{index}.png")
        return doc.page_count


@router.post(
    "/tenants/{tenant_id}/templates",
    response_model=schemas.TemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_template(
    tenant_id: int,
    name: str = Form(...),
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if document_type not in VALID_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"document_type must be one of {sorted(VALID_DOCUMENT_TYPES)}",
        )
    tenant_exists = db.execute(
        text("SELECT 1 FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    ).scalar()
    if not tenant_exists:
        raise HTTPException(status_code=404, detail="Tenant not found")

    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File type {extension or '(none)'} not allowed — use one of {sorted(ALLOWED_EXTENSIONS)}",
        )

    content = file.file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    template = Template(
        tenant_id=tenant_id,
        name=name,
        document_type=document_type,
        file_path="",  # filled in below once we know the template id
        page_count=1,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    tdir = template_dir(template.id)
    try:
        tdir.mkdir(parents=True, exist_ok=True)
        original = tdir / f"original{extension}"
        original.write_bytes(content)
        page_count = _render_pages(original, tdir / "pages")

        template.file_path = str(original)
        template.page_count = page_count
        db.commit()
        db.refresh(template)
    except Exception as exc:  # noqa: BLE001 — roll back the row + files on any render failure
        logger.exception("Template render failed")
        db.delete(template)
        db.commit()
        shutil.rmtree(tdir, ignore_errors=True)
        raise HTTPException(
            status_code=422,
            detail=f"Could not read the uploaded document ({exc}). Is the file a valid PDF/image?",
        )

    return template


@router.get("/tenants/{tenant_id}/templates", response_model=list[schemas.TemplateRead])
def list_templates(tenant_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Template)
        .filter(Template.tenant_id == tenant_id)
        .order_by(Template.created_at.desc())
        .all()
    )


@router.get("/templates/{template_id}", response_model=schemas.TemplateRead)
def get_template(template_id: int, db: Session = Depends(get_db)):
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/templates/{template_id}/pages/{page_number}")
def get_template_page_preview(template_id: int, page_number: int, db: Session = Depends(get_db)):
    """Returns the rendered PNG preview for one page of the uploaded template."""
    template = db.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if page_number < 1 or page_number > template.page_count:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_number} out of range (template has {template.page_count} page(s)).",
        )
    image = template_dir(template_id) / "pages" / f"page_{page_number}.png"
    if not image.exists():
        raise HTTPException(status_code=404, detail="Rendered page image missing on disk.")
    return FileResponse(image, media_type="image/png")
