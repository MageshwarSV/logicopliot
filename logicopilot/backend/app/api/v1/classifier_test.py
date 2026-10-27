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

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

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


_UI_PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Classifier Test</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  textarea { width: 100%; font-family: monospace; font-size: 12px; }
  label { display: block; margin-top: 1rem; font-weight: 600; }
  button { margin-top: 1rem; padding: 0.5rem 1.2rem; font-size: 14px; cursor: pointer; }
  pre { background: #f4f4f4; padding: 1rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  #status { margin-top: 1rem; font-weight: 600; }
  ul#queue { padding-left: 1.2rem; }
</style>
</head>
<body>
<h1>Classifier Test</h1>
<p>Pick several files at once - each is sent to the real classifier, one at a time, and every
result is shown together at the end.</p>

<label for="files">Files</label>
<input type="file" id="files" multiple>
<ul id="queue"></ul>

<label for="candidates">Candidates JSON</label>
<textarea id="candidates" rows="6">__DEFAULT_CANDIDATES__</textarea>

<label for="mode">Prompt mode</label>
<select id="mode">
  <option value="simple" selected>simple - bare-minimum question, no hints/traps</option>
  <option value="production">production - the real Smart Upload prompt</option>
</select>

<button id="run">Run</button>
<div id="status"></div>
<pre id="result"></pre>

<script>
const filesInput = document.getElementById('files');
const queueList = document.getElementById('queue');
filesInput.addEventListener('change', () => {
  queueList.innerHTML = '';
  for (const f of filesInput.files) {
    const li = document.createElement('li');
    li.textContent = f.name;
    queueList.appendChild(li);
  }
});

document.getElementById('run').addEventListener('click', async () => {
  const statusEl = document.getElementById('status');
  const resultEl = document.getElementById('result');
  const files = filesInput.files;
  if (!files.length) {
    statusEl.textContent = 'Pick at least one file first.';
    return;
  }
  statusEl.textContent = `Classifying ${files.length} file(s)...`;
  resultEl.textContent = '';

  const form = new FormData();
  for (const f of files) form.append('files', f);
  form.append('candidates_json', document.getElementById('candidates').value);
  form.append('prompt_mode', document.getElementById('mode').value);

  try {
    const resp = await fetch('/api/v1/classifier-test', {
      method: 'POST',
      body: form,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
    const body = await resp.json();
    if (!resp.ok) {
      statusEl.textContent = `Error (${resp.status})`;
      resultEl.textContent = JSON.stringify(body, null, 2);
      return;
    }
    statusEl.textContent = 'Done.';
    resultEl.textContent = JSON.stringify(body, null, 2);
  } catch (err) {
    statusEl.textContent = 'Request failed.';
    resultEl.textContent = String(err);
  }
});
</script>
</body>
</html>
"""


@router.get("/ui", response_class=HTMLResponse)
def classifier_test_ui(_user=Depends(require_role(SUPER_ADMIN))) -> str:
    """A real multi-select file picker, since Swagger's own "Try it out" form can't render one
    for this endpoint (see classify_one_file's own comment) - a plain page, same backend, same
    session cookie, no separate login. Each selected file is sent to classify_one_file in ONE
    request (the server already classifies them one at a time internally - see its own loop);
    all results come back together and are shown as raw JSON once the request completes."""
    return _UI_PAGE_HTML.replace("__DEFAULT_CANDIDATES__", json.dumps(DEFAULT_CANDIDATES, indent=2))


@router.post("")
def classify_one_file(
    # A plain array field - this is the right shape for a real HTTP client (curl, fetch, the
    # dedicated /classifier-test/ui page below), which can attach several files under "files"
    # with no trouble at all. Swagger's own "Try it out" widget can't render an ARRAY of files
    # as a multi-select picker under this app's OpenAPI 3.1 output (confirmed against the live
    # schema: a single file field renders fine, an array of the same type falls back to plain
    # text boxes per item) - that is a Swagger-UI rendering limitation, not a problem with this
    # endpoint, so use /classifier-test/ui for a real multi-select picker instead of Swagger's
    # own form for this one.
    files: list[UploadFile] = File(..., description="One or more files - each is classified independently."),
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
    if not files:
        raise HTTPException(status_code=422, detail="Upload at least one file.")

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
