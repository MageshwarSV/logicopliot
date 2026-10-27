"""Standalone classifier test tool - upload any one file and see exactly what the real Smart
Upload classifier (app.core.classifier.classify_page_image) decides, with full
confidence/evidence per page, without creating a job or touching any real data at all.

Super-Admin-only, same as every other admin tool in this app - no separate local-only flag:
runs on UAT using UAT's own already-configured OpenAI key pool (tenant_id=None, the same
global-pool fallback every other untenanted call already uses), reachable straight from
/docs with an ordinary logged-in Super Admin session.

`x_requested_with` is declared as an explicit header parameter (rather than left implicit)
so FastAPI/Swagger shows it as a fillable field in "Try it out" - the app's own CSRF
middleware (require_custom_header_on_mutations in app/main.py) 403s any POST missing this
header, and Swagger's own request builder does not set it automatically the way the app's
real frontend (every fetch/XHR call) does.
"""
import base64
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app.core import office_convert
from app.core.classifier import classify_page_image
from app.core.config import get_settings
from app.core.deps import require_role
from app.models.user import SUPER_ADMIN

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/classifier-test", tags=["classifier-test"])

# A sensible default matching 4S Logistics' Sea Import template, so a quick try needs no setup -
# override by passing your own `candidates_json` for any other template's document types.
DEFAULT_CANDIDATES = [
    {"key": "BL", "name": "Bill of lading", "doc_type": "BL"},
    {"key": "Invoice", "name": "Invoice", "doc_type": "Invoice"},
    {"key": "PackingList", "name": "Packing List", "doc_type": "PackingList"},
    {"key": "Freight", "name": "Fright Certificate", "doc_type": "Custom"},
]
MAX_PAGES = 10  # same ballpark cap Smart Upload itself uses for classification
MAX_TEST_FILES = 5  # see the file_1..file_5 params below for why this is fixed, not a list


def _simple_classify_page(image_path: Path, candidates: list[dict], model: str) -> list[dict]:
    """The bare-minimum prompt, exactly as requested - no hints, no traps, no per-candidate
    description, just the plain question - to compare against classify_page_image's own
    (deliberately still fairly plain, but hint- and trap-augmented) production prompt. A
    standalone copy, not a mode of classify_page_image itself - this is a test-only variant,
    never used by Smart Upload or any real job."""
    from openai import APIError

    from app.core.llm import create_chat_completion_with_retry, reasoning_safe_chat_params

    names = ", ".join(c["name"] for c in candidates)
    instruction = (
        f"Please clarify the document type: Is it a {names}, or any other document?\n\n"
        'Return JSON: {"document_type": "<one of the listed names, or "Other">", '
        '"confidence": 0.00}.'
    )
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    content = [
        {"type": "text", "text": instruction},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]
    try:
        resp = create_chat_completion_with_retry(
            timeout=60,
            model=model,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
            **reasoning_safe_chat_params(model, temperature=0, max_tokens=300),
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except APIError as exc:
        return [{"key": None, "confidence": 0.0, "evidence": f"AI call failed: {exc}"}]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Simple-prompt classification failed: %s", exc)
        return [{"key": None, "confidence": 0.0, "evidence": f"Error: {exc}"}]
    doc_type = str(data.get("document_type") or "").strip()
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    name_to_key = {c["name"]: c["key"] for c in candidates}
    return [{
        "key": name_to_key.get(doc_type, doc_type or None),
        "confidence": confidence,
        # This bare-minimum prompt never asks the model to justify itself - its raw answer
        # (the document_type string it actually returned) is shown here instead of a reasoned
        # "evidence" explanation, since there isn't one.
        "evidence": f"model answered document_type={doc_type!r}",
    }]


@router.post("")
def classify_one_file(
    # Individual optional slots, not a `list[UploadFile]` array field: FastAPI/Pydantic here
    # emit OpenAPI 3.1, which marks a file as {"type": "string", "contentMediaType":
    # "application/octet-stream"} - Swagger UI renders that correctly as a file picker for a
    # SINGLE field (every other upload endpoint in this app proves that), but not for an ARRAY
    # of them - it falls back to plain text boxes for each array item. Found live: switching
    # between `list[UploadFile]` and `typing.List[UploadFile]` made no difference at all, since
    # both produce the identical array schema Swagger can't render file pickers for. Five
    # separate optional fields each get their own real file picker, matching how the original
    # single-file version already worked, at the cost of a fixed (not arbitrary) upload count.
    file_1: Optional[UploadFile] = File(default=None, description="File 1 (required)."),
    file_2: Optional[UploadFile] = File(default=None, description="File 2 (optional)."),
    file_3: Optional[UploadFile] = File(default=None, description="File 3 (optional)."),
    file_4: Optional[UploadFile] = File(default=None, description="File 4 (optional)."),
    file_5: Optional[UploadFile] = File(default=None, description="File 5 (optional)."),
    candidates_json: str = Form(
        default=json.dumps(DEFAULT_CANDIDATES),
        description=(
            "JSON list of {\"key\", \"name\", \"doc_type\"} - the document types to classify "
            "against. Defaults to 4S Logistics' Sea Import template's own 4 types."
        ),
    ),
    prompt_mode: str = Form(
        default="simple",
        description=(
            '"simple" - the bare-minimum prompt ("Please clarify the document type: Is it a '
            'Bill of Lading, Invoice, Packing List, Freight Certificate, or any other '
            "document?\"), no hints or trap warnings at all. \"production\" - the real "
            "classify_page_image prompt Smart Upload actually uses, hints/traps included."
        ),
    ),
    x_requested_with: str = Header(
        default="XMLHttpRequest",
        description="Required by this app's own CSRF check on every POST - leave as XMLHttpRequest.",
    ),
    _user=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Upload one or more PDF/PNG/JPG/Word/Excel files, get back exactly what the real
    classifier decides for EVERY page of EVERY file: which candidate(s) it matches (if any),
    and the confidence/evidence behind each - the same classify_page_image call Smart Upload
    itself makes per page, just shown raw per page here instead of merged into page-range
    groups (which is what the real pipeline does next, but that step throws the confidence
    number away - kept here since that's exactly what a test tool is for). Each uploaded file
    is classified independently (no cross-file "specificity" contest the way Smart Upload's own
    multi-file batch does - this is a per-file test, not a job simulation). Uses this server's
    own configured OpenAI key pool (tenant_id=None - the same global-pool fallback every
    untenanted call already uses), so it works out of the box wherever it's deployed, no
    separate key setup needed."""
    try:
        candidates = json.loads(candidates_json)
        if not isinstance(candidates, list) or not candidates:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="candidates_json must be a non-empty JSON list of {key, name, doc_type}."
        ) from exc
    if prompt_mode not in ("simple", "production"):
        raise HTTPException(status_code=422, detail='prompt_mode must be "simple" or "production".')
    files = [f for f in (file_1, file_2, file_3, file_4, file_5) if f is not None]
    if not files:
        raise HTTPException(status_code=422, detail="Upload at least one file (file_1).")

    model = get_settings().vision_engine_model
    key_to_name = {c["key"]: c["name"] for c in candidates}
    work_dir = Path(get_settings().uploads_dir) / "_classifier_test" / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        results = []
        for upload in files:
            name = upload.filename or "upload"
            content = upload.file.read()
            if not content:
                results.append({"file": name, "error": "Uploaded file is empty."})
                continue

            file_dir = work_dir / uuid.uuid4().hex
            file_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(name).suffix.lower()
            src = file_dir / f"upload{ext}"
            src.write_bytes(content)

            # Word/Excel converted first, exactly like Smart Upload - classify the resulting
            # PDF(s), not the original file.
            pdfs: list[tuple[str, Path]] = []
            try:
                if ext in office_convert.WORD_EXTS:
                    pdfs.append((name, office_convert.convert_word_to_pdf(src, file_dir, file_dir)))
                elif ext in office_convert.EXCEL_EXTS:
                    for sheet_name, pdf in office_convert.convert_excel_sheets_to_pdfs(src, file_dir, file_dir):
                        pdfs.append((f"{Path(name).stem} - {sheet_name}.pdf", pdf))
                else:
                    pdfs.append((name, src))
            except office_convert.OfficeConversionFailed as exc:
                results.append({"file": name, "error": str(exc)})
                continue

            for pdf_name, pdf_path in pdfs:
                pages_dir = file_dir / f"pages_{uuid.uuid4().hex}"
                pages_dir.mkdir(parents=True, exist_ok=True)
                page_images: list[tuple[int, Path]] = []
                with fitz.open(pdf_path) as doc:
                    for i, page in enumerate(doc, start=1):
                        if i > MAX_PAGES:
                            break
                        png = pages_dir / f"page_{i}.png"
                        page.get_pixmap(dpi=150).save(png)
                        page_images.append((i, png))

                prev_keys: list[str] = []
                pages_out = []
                for page_no, image_path in page_images:
                    if prompt_mode == "simple":
                        matches = [m for m in _simple_classify_page(image_path, candidates, model) if m["key"]]
                    else:
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
        return {
            "model": model,
            "prompt_mode": prompt_mode,
            "candidates": [c["name"] for c in candidates],
            "results": results,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
