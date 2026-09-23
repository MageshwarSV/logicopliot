import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user, get_db, require_role
from app.models.template_document import TemplateDocument
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.onboarding import DocumentDetailOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/template-documents", tags=["onboarding: documents"])

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_FILE_BYTES = 25 * 1024 * 1024
RENDER_DPI = 150


def document_dir(document_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "documents" / document_id


def _render_pages(original: Path, pages_dir: Path) -> int:
    import fitz  # PyMuPDF

    pages_dir.mkdir(parents=True, exist_ok=True)
    MAX_RENDER_SIDE = 2500
    with fitz.open(original) as doc:
        for index, page in enumerate(doc, start=1):
            longest_pt = max(page.rect.width, page.rect.height) or 1
            dpi = min(RENDER_DPI, 72 * MAX_RENDER_SIDE / longest_pt)
            page.get_pixmap(dpi=dpi).save(pages_dir / f"page_{index}.png")
        return doc.page_count


@router.post("/{document_id}/upload", response_model=DocumentDetailOut)
def upload_document_file(
    document_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> DocumentDetailOut:
    """Step 2: attach a sample file to a declared document and render page previews."""
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type not allowed — use one of {sorted(ALLOWED_EXTENSIONS)}",
        )

    # Reject oversized uploads BEFORE reading the whole body into RAM (Starlette
    # populates UploadFile.size from the spooled temp file).
    if file.size is not None and file.size > MAX_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit.")

    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 25 MB limit.")
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty.")

    ddir = document_dir(document_id)
    try:
        ddir.mkdir(parents=True, exist_ok=True)
        original = ddir / f"original{extension}"
        original.write_bytes(content)
        page_count = _render_pages(original, ddir / "pages")
    except Exception:  # noqa: BLE001 — log detail server-side, return a generic message
        logger.exception("Failed to render uploaded document %s", document_id)
        shutil.rmtree(ddir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not read the uploaded document. Is it a valid PDF or image?",
        )

    document.file_path = str(original)
    document.page_count = page_count
    db.commit()
    db.refresh(document)
    return DocumentDetailOut.model_validate(document)


class RequiredPayload(BaseModel):
    is_required: bool


@router.patch("/{document_id}/required", response_model=DocumentDetailOut)
def set_document_required(
    document_id: str,
    payload: RequiredPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> DocumentDetailOut:
    """Mandatory (the default) or optional: whether a job can pass Document Capture
    without this document ever being uploaded."""
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if user.role == TENANT_ADMIN and document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    document.is_required = payload.is_required
    db.commit()
    db.refresh(document)
    return DocumentDetailOut.model_validate(document)


class NamePayload(BaseModel):
    name: str


@router.patch("/{document_id}/name", response_model=DocumentDetailOut)
def rename_document(
    document_id: str,
    payload: NamePayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> DocumentDetailOut:
    """The label shown everywhere a document is listed — Document Capture, the extracted-data
    screen, cross-checks. Set once at template creation (a short form like "INV" or "PL" was
    never revisited); this is the only way to fix it afterward without touching every job that
    already used the old name — those keep their own recorded label, only the template
    changes."""
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if user.role == TENANT_ADMIN and document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name cannot be blank.")
    document.name = name[:200]
    db.commit()
    db.refresh(document)
    return DocumentDetailOut.model_validate(document)


class DocTypePayload(BaseModel):
    doc_type: str


@router.patch("/{document_id}/doc-type", response_model=DocumentDetailOut)
def set_document_type(
    document_id: str,
    payload: DocTypePayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> DocumentDetailOut:
    """Which kind of document this slot is ("BL", "Invoice", "PackingList", "Custom") —
    read live off every job that uses this slot (JobDocumentOut.doc_type comes straight
    from here, never a copy stored per job), so fixing it here fixes it everywhere that
    slot has ever been used, past jobs included, in one write."""
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if user.role == TENANT_ADMIN and document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    doc_type = payload.doc_type.strip()
    if not doc_type:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="doc_type cannot be blank.")
    document.doc_type = doc_type[:30]
    db.commit()
    db.refresh(document)
    return DocumentDetailOut.model_validate(document)


@router.get("/{document_id}/pages/{page_number}")
def get_document_page(
    document_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN, OPERATOR)),
):
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    # Non-super-admins may only view their own tenant's document previews.
    if user.role != SUPER_ADMIN and document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if page_number < 1 or page_number > document.page_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page out of range")
    image = document_dir(document_id) / "pages" / f"page_{page_number}.png"
    if not image.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered page missing")
    return FileResponse(image, media_type="image/png")
