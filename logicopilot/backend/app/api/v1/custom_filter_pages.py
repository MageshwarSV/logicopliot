import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.custom_page_filter import classify_custom_page
from app.core.deps import get_db, require_role
from app.core.docai import ocr_page_image
from app.models.custom_filter_page import CustomFilterPage
from app.models.job import Job, JobDocument
from app.models.user import SUPER_ADMIN, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-filter-pages", tags=["custom filter pages"])

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_FILE_BYTES = 50 * 1024 * 1024
# Quality for the STORED preview copy only - OCR always reads the full-quality PNG render
# first (see upload_custom_filter_page), so recompressing afterward never affects what the
# filter actually matches against, only how much disk space the preview image takes. 90 is
# visually indistinguishable from the original for a scanned document page while typically
# cutting file size by 80-95% versus lossless PNG.
PREVIEW_JPEG_QUALITY = 90


class CustomFilterPageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    original_filename: str | None
    page_count: int
    is_active: bool
    uploaded_by: str | None
    created_at: datetime
    updated_at: datetime


class ActiveFlag(BaseModel):
    is_active: bool


def _dir(row_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "custom_filter_pages" / row_id


def _compress_preview_image(png_path: Path) -> None:
    """Re-save an already-OCR'd page render as a high-quality JPEG in place of the lossless
    PNG, then remove the PNG - cuts disk footprint dramatically (a scanned page is typically
    80-95% smaller as JPEG) with no visible quality loss, and with zero effect on matching
    accuracy since OCR already ran against the original PNG before this is ever called."""
    import fitz  # PyMuPDF

    pix = fitz.Pixmap(str(png_path))
    jpg_path = png_path.with_suffix(".jpg")
    pix.save(str(jpg_path), jpg_quality=PREVIEW_JPEG_QUALITY)
    png_path.unlink()


def _sweep_old_jobs_for_reference(db: Session, reference_text: str) -> None:
    """When a new filter page is added, old jobs already carry the same junk this would have
    kept out going forward - this applies the exact same content match retroactively.

    Every already-extracted document is checked page-by-page against ONLY this new
    reference (older references already had their own sweep when THEY were added - nothing
    needs to re-run for those). A document with any matching page is removed via the same
    path an operator uses to delete a wrong upload; a job left with no real documents at all
    afterward is deleted entirely - the identical two actions performed by hand earlier this
    session (172 documents, then 4 jobs), now automatic.
    """
    from app.api.v1.jobs import _delete_job_cascade, _remove_document_file

    jobs = db.query(Job).filter(Job.status.notin_(("draft", "extracting"))).all()
    docs_removed = jobs_deleted = 0

    for job in jobs:
        try:
            job_docs = db.query(JobDocument).filter(JobDocument.job_id == job.id).all()
            for jd in job_docs:
                ej = jd.extracted_json
                if not isinstance(ej, dict):
                    continue
                pages = ej.get("pages") or []
                if not any(classify_custom_page(p, [reference_text])[0] for p in pages):
                    continue
                logger.info("filter-page sweep: removing %s (job %s) - content matches the "
                            "new reference page", jd.original_name, job.reference)
                _remove_document_file(db, job, jd)
                docs_removed += 1

            db.flush()
            remaining = (
                db.query(JobDocument)
                .filter(JobDocument.job_id == job.id, JobDocument.file_path.isnot(None))
                .count()
            )
            if remaining == 0 and job_docs:
                # job_docs non-empty guards a job that never had any real files to begin
                # with (nothing to sweep, and not this sweep's business to delete it).
                logger.info("filter-page sweep: deleting job %s - nothing real left after "
                            "the sweep above", job.reference)
                _delete_job_cascade(db, job)
                jobs_deleted += 1
            # Commit PER JOB, not once at the end: a later job's failure below must roll
            # back only ITS OWN partial changes, never undo every job already processed.
            db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("filter-page sweep: failed on job %s - skipping, continuing "
                             "with the rest", job.reference)
            db.rollback()

    logger.info("filter-page sweep complete: %d document(s) removed, %d job(s) deleted",
                docs_removed, jobs_deleted)


def _start_sweep_background(reference_text: str) -> None:
    """Run the old-job sweep off the request thread, so uploading a filter page returns at
    once instead of sitting on a scan of every job in the system. Own DB session, same
    pattern as _start_extraction_background in jobs.py."""
    import threading

    from app.db.session import SessionLocal

    def _go() -> None:
        db2 = SessionLocal()
        try:
            _sweep_old_jobs_for_reference(db2, reference_text)
        except Exception:  # noqa: BLE001
            logger.exception("filter-page sweep failed")
            db2.rollback()
        finally:
            db2.close()

    threading.Thread(target=_go, daemon=True, name="filter-page-sweep").start()


@router.get("", response_model=list[CustomFilterPageOut])
def list_custom_filter_pages(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[CustomFilterPage]:
    return db.query(CustomFilterPage).order_by(CustomFilterPage.created_at.desc()).all()


@router.post("", response_model=CustomFilterPageOut, status_code=status.HTTP_201_CREATED)
def upload_custom_filter_page(
    name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> CustomFilterPage:
    """Upload a reference page (PDF/image) - OCR'd once, right here, so its text can be
    compared against every future ingested document page by pure string similarity (see
    app/core/custom_page_filter.py). Never re-OCR'd after this; the OCR text is what gets
    matched against, forever, until this row is deleted or replaced.
    """
    from app.api.v1.jobs import _render_pages  # reused, not reimplemented — see jobs.py

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"File type not allowed — use one of {sorted(ALLOWED_EXTENSIONS)}")
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    row = CustomFilterPage(
        name=name,
        reference_text="",
        original_filename=(file.filename or "")[:255] or None,
        uploaded_by=user.id,
    )
    db.add(row)
    db.flush()  # need row.id for its own folder

    ddir = _dir(row.id)
    try:
        ddir.mkdir(parents=True, exist_ok=True)
        original = ddir / f"original{ext}"
        original.write_bytes(content)
        page_count = _render_pages(original, ddir / "pages")
        page_texts = []
        for i in range(1, page_count + 1):
            png_path = ddir / "pages" / f"page_{i}.png"
            # OCR the full-quality render FIRST - everything below only touches the STORED
            # copy used for the admin preview, never what the filter actually matched on.
            page_texts.append(ocr_page_image(png_path).get("text", ""))
            _compress_preview_image(png_path)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to OCR uploaded custom filter page")
        db.rollback()
        shutil.rmtree(ddir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Could not read/OCR the uploaded document. Is it a valid PDF or image?")

    reference_text = "\n".join(page_texts)
    if not reference_text.strip():
        db.rollback()
        shutil.rmtree(ddir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="OCR found no text on this page — nothing to match against.")

    row.reference_text = reference_text
    row.page_count = len(page_texts)
    db.commit()
    db.refresh(row)
    _start_sweep_background(row.reference_text)
    return row


@router.get("/{page_id}/pages/{page_number}")
def get_custom_filter_page_image(
    page_id: str,
    page_number: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    """Serve the rendered page image, so an admin can see what they uploaded/are matching
    against — same pattern as a job document's page preview."""
    row = db.get(CustomFilterPage, page_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if page_number < 1 or page_number > row.page_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page out of range")
    base = _dir(row.id) / "pages" / f"page_{page_number}"
    # .jpg is what every upload produces now (see _compress_preview_image); .png is kept as
    # a fallback so a reference page uploaded before this change still previews correctly.
    for ext, media_type in ((".jpg", "image/jpeg"), (".png", "image/png")):
        image = base.with_suffix(ext)
        if image.exists():
            return FileResponse(image, media_type=media_type)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered page missing")


@router.patch("/{page_id}/active", response_model=CustomFilterPageOut)
def set_custom_filter_page_active(
    page_id: str,
    payload: ActiveFlag,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> CustomFilterPage:
    row = db.get(CustomFilterPage, page_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_filter_page(
    page_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> None:
    row = db.get(CustomFilterPage, page_id)
    if row is None:
        return
    shutil.rmtree(_dir(row.id), ignore_errors=True)
    db.delete(row)
    db.commit()
