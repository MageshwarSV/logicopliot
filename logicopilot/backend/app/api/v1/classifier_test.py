"""Standalone classifier test tool - upload any one file and see exactly what the real Smart
Upload classifier (app.core.classifier.classify_pages_from_images) decides, with full
confidence/evidence per match, without creating a job or touching any real data at all.

LOCAL USE ONLY, by design: every route here 404s unless settings.enable_local_classifier_test
is explicitly turned on (see app/core/config.py's own comment on that flag) - off by default
everywhere, including UAT and production, so this file can safely sit in the normal codebase
and even get deployed by accident without doing anything there.

No auth is required beyond that flag - this is meant to be hit straight from a browser at
http://localhost:8000/docs while running the backend locally with your own OpenAI key in a
local .env, not something exposed anywhere a real session matters.
"""
import json
import logging
import shutil
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core import office_convert
from app.core.classifier import classify_page_image
from app.core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/classifier-test", tags=["classifier-test (local only)"])

# A sensible default matching 4S Logistics' Sea Import template, so a quick try needs no setup -
# override by passing your own `candidates_json` for any other template's document types.
DEFAULT_CANDIDATES = [
    {"key": "BL", "name": "Bill of lading", "doc_type": "BL"},
    {"key": "Invoice", "name": "Invoice", "doc_type": "Invoice"},
    {"key": "PackingList", "name": "Packing List", "doc_type": "PackingList"},
    {"key": "Freight", "name": "Fright Certificate", "doc_type": "Custom"},
]
MAX_PAGES = 10  # same ballpark cap Smart Upload itself uses for classification


def _require_enabled() -> None:
    if not get_settings().enable_local_classifier_test:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


@router.post("")
def test_classify_one_file(
    file: UploadFile = File(...),
    candidates_json: str = Form(
        default=json.dumps(DEFAULT_CANDIDATES),
        description=(
            "JSON list of {\"key\", \"name\", \"doc_type\"} - the document types to classify "
            "against. Defaults to 4S Logistics' Sea Import template's own 4 types."
        ),
    ),
) -> dict:
    """Upload one PDF/PNG/JPG/Word/Excel file, get back exactly what the real classifier
    decides for EVERY page: which candidate(s) it matches (if any), and the confidence/evidence
    behind each - the same classify_page_image call Smart Upload itself makes per page, just
    shown raw per page here instead of merged into page-range groups (which is what the real
    pipeline does next, but that step throws the confidence number away - kept here since
    that's exactly what a test tool is for)."""
    _require_enabled()

    try:
        candidates = json.loads(candidates_json)
        if not isinstance(candidates, list) or not candidates:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="candidates_json must be a non-empty JSON list of {key, name, doc_type}."
        ) from exc

    name = file.filename or "upload"
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    work_dir = Path(get_settings().uploads_dir) / "_classifier_test" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        ext = Path(name).suffix.lower()
        src = work_dir / f"upload{ext}"
        src.write_bytes(content)

        # Word/Excel converted first, exactly like Smart Upload - classify the resulting PDF(s),
        # not the original file.
        pdfs: list[tuple[str, Path]] = []
        if ext in office_convert.WORD_EXTS:
            pdfs.append((name, office_convert.convert_word_to_pdf(src, work_dir, work_dir)))
        elif ext in office_convert.EXCEL_EXTS:
            for sheet_name, pdf in office_convert.convert_excel_sheets_to_pdfs(src, work_dir, work_dir):
                pdfs.append((f"{Path(name).stem} - {sheet_name}.pdf", pdf))
        else:
            pdfs.append((name, src))

        model = get_settings().vision_engine_model
        results = []
        for pdf_name, pdf_path in pdfs:
            pages_dir = work_dir / f"pages_{uuid.uuid4().hex}"
            pages_dir.mkdir(parents=True, exist_ok=True)
            page_images: list[tuple[int, Path]] = []
            with fitz.open(pdf_path) as doc:
                for i, page in enumerate(doc, start=1):
                    if i > MAX_PAGES:
                        break
                    png = pages_dir / f"page_{i}.png"
                    page.get_pixmap(dpi=150).save(png)
                    page_images.append((i, png))

            key_to_name = {c["key"]: c["name"] for c in candidates}
            prev_keys: list[str] = []
            pages_out = []
            for page_no, image_path in page_images:
                matches = classify_page_image(pdf_name, page_no, image_path, candidates, model, prev_keys)
                pages_out.append({
                    "page": page_no,
                    "matches": [
                        {
                            "matched_type": key_to_name.get(m["key"], m["key"]),
                            "confidence": m["confidence"],
                            "evidence": m["evidence"],
                        }
                        for m in matches
                    ] or None,
                })
                prev_keys = [m["key"] for m in matches]
            results.append({
                "file": pdf_name,
                "pages_checked": len(page_images),
                "pages": pages_out,
            })
        return {"model": model, "candidates": [c["name"] for c in candidates], "results": results}
    except office_convert.OfficeConversionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
