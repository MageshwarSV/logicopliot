"""The Fix screen (Super Admin -> Extraction Corrections): improve ONE field's extraction prompt
from the corrections operators made to it.

  info()      the field's current prompt (and format hint), whether it can be edited, its history
  suggest()   ask the extraction model to analyse the AI-vs-corrected values (and the pages they
              were read from) and propose a better prompt - shown, never saved
  test()      read the field again on the latest corrected jobs with a candidate prompt and
              compare with what the operator saved - read-only, no job is changed
  apply()     save the prompt through the template's own update (field_marks.edit_mark /
              edit_custom_field), keeping the old one in prompt_changes
  revert()    put an earlier prompt back (recorded as a change of its own)

The model is the one extraction itself uses (by request): the prompt is written for the model
that will run it. Only fields with a prompt of their own can be fixed: a field read off a
document (FieldMark.extraction_prompt + value_format_hint) or a computed AI field
(CustomField.ai_prompt). Lookups, operator-entry and composite fields have none.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SAMPLE_JOBS = 5          # jobs whose pages go to AI Suggestion and which Test re-reads
MAX_IMAGES = 8           # page images in one suggestion request
MAX_PAIRS = 200          # before/after pairs in one suggestion request
TEST_WORKERS = 5         # documents re-read at the same time by Test

NOT_EDITABLE = {
    "lookup": "Its value comes from the reference sheet or saved values, not from a prompt.",
    "hardcoded": "The operator fills it (or it holds a fixed value); the AI does not read it.",
    "composite": "It is built from other fields; fix those fields instead.",
}


# ---------------------------------------------------------------------------- the field

def resolve(db: Session, group_id: str, template_document_id: str | None, label_name: str):
    """(kind, obj): ("mark", FieldMark) | ("custom", CustomField). 404 when it no longer exists."""
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark

    if template_document_id:
        mark = (db.query(FieldMark).filter(FieldMark.document_id == template_document_id,
                                           FieldMark.label_name == label_name).first())
        if mark is not None:
            return "mark", mark
    cf = (db.query(CustomField).filter(CustomField.group_id == group_id,
                                       CustomField.label_name == label_name).first())
    if cf is not None:
        return "custom", cf
    raise HTTPException(status_code=404, detail=f"The field {label_name!r} is no longer in this template.")


def _editable(kind: str, obj) -> tuple[bool, str | None]:
    if kind == "mark":
        return True, None
    if obj.kind == "ai":
        return True, None
    return False, NOT_EDITABLE.get(obj.kind, "This field has no prompt of its own.")


def _current(kind: str, obj) -> tuple[str, str | None]:
    if kind == "mark":
        return obj.extraction_prompt or "", obj.value_format_hint
    return obj.ai_prompt or "", None


def extraction_model(db: Session) -> tuple[str, bool]:
    """(model, reads_images) - what run_extraction uses for a document field: the vision model
    when the platform's extraction engine is set to read page images, else the text model."""
    from app.core.config import get_settings
    from app.core.system_settings import get_extraction_engine

    settings = get_settings()
    if get_extraction_engine(db) == "gpt5_mini_vision":
        return settings.vision_engine_model, True
    return settings.openai_model, False


def info(db: Session, group_id: str, template_document_id: str | None, label_name: str) -> dict:
    from app.models.prompt_change import PromptChange

    kind, obj = resolve(db, group_id, template_document_id, label_name)
    editable, reason = _editable(kind, obj)
    prompt, hint = _current(kind, obj)
    model, images = extraction_model(db)
    q = db.query(PromptChange)
    q = q.filter(PromptChange.mark_id == obj.id) if kind == "mark" else q.filter(PromptChange.custom_field_id == obj.id)
    history = [{"id": c.id, "at": c.created_at.isoformat(), "by": c.changed_by_name, "action": c.action,
                "old_prompt": c.old_prompt, "new_prompt": c.new_prompt,
                "old_format_hint": c.old_format_hint, "new_format_hint": c.new_format_hint}
               for c in q.order_by(PromptChange.created_at.desc()).limit(20).all()]
    return {
        "kind": kind, "field_id": obj.id, "label_name": label_name,
        "editable": editable, "reason": reason,
        "prompt": prompt, "format_hint": hint, "has_format_hint": kind == "mark",
        "format_rule": getattr(obj, "tenant_format_prompt", None) if kind == "mark" else None,
        "multi_value": bool(getattr(obj, "is_multi_value", False)) if kind == "mark" else bool(getattr(obj, "per_row", False)),
        "model": model, "reads_images": images,
        "test_supported": kind == "mark" or not getattr(obj, "per_row", False),
        "history": history,
    }


# ---------------------------------------------------------------------------- the evidence

def _corrected_lines(db: Session, group_id, template_document_id, label_name) -> list[dict]:
    from app.core import field_edits

    return field_edits.edits(db, group_id=group_id, label_name=label_name,
                             template_document_id=template_document_id)["lines"]


def _samples(lines: list[dict]) -> list[dict]:
    """The latest SAMPLE_JOBS jobs that still exist - every corrected line of each."""
    jobs: list[str] = []
    for line in lines:
        if line["job_exists"] and line["job_id"] not in jobs:
            jobs.append(line["job_id"])
        if len(jobs) == SAMPLE_JOBS:
            break
    return [line for line in lines if line["job_id"] in jobs]


def _doc_dir(job_document_id: str) -> Path:
    from app.core.config import get_settings

    return Path(get_settings().uploads_dir) / "jobs" / job_document_id


def _doc_text(jd) -> str:
    """The document's OCR text, read the way extraction reads it (cached page OCR, repeated
    page headers stripped). Excel sheets are read from their cells."""
    from app.core.docai import get_page_ocr
    from app.core.page_filter import strip_repeated_page_header

    if getattr(jd, "source_kind", "pdf") == "excel":
        from app.api.v1.jobs import _excel_sheet_text

        return _excel_sheet_text(jd)
    parts = []
    for page in range(1, (jd.page_count or 0) + 1):
        try:
            ocr = get_page_ocr(_doc_dir(jd.id), page)
            parts.append(ocr.get("layout_text") or ocr.get("text", ""))
        except Exception:  # noqa: BLE001
            parts.append("")
    return "\n".join(strip_repeated_page_header(parts))


def _pages(jd, found_page: int | None = None) -> list[Path]:
    if getattr(jd, "source_kind", "pdf") == "excel":
        return []
    want = [found_page] if found_page else list(range(1, min(jd.page_count or 0, 2) + 1))
    out = [_doc_dir(jd.id) / "pages" / f"page_{p}.png" for p in want if p]
    return [p for p in out if p.exists()]


def _all_pages(jd) -> list[Path]:
    if getattr(jd, "source_kind", "pdf") == "excel":
        return []
    out = [_doc_dir(jd.id) / "pages" / f"page_{p}.png" for p in range(1, (jd.page_count or 0) + 1)]
    return [p for p in out if p.exists()]


def _source_docs(db: Session, kind: str, obj, job_id: str, job_document_id: str | None) -> list:
    from app.models.job import JobDocument

    if kind == "mark" and job_document_id:
        jd = db.get(JobDocument, job_document_id)
        return [jd] if jd is not None and jd.file_path else []
    q = db.query(JobDocument).filter(JobDocument.job_id == job_id, JobDocument.file_path.isnot(None))
    src = [s for s in (getattr(obj, "source_document_ids", None) or []) if s]
    if src:
        q = q.filter(JobDocument.template_document_id.in_(src))
    return q.all()


def _found_page(db: Session, line: dict, label_name: str) -> int | None:
    from app.models.job import JobFieldValue

    q = db.query(JobFieldValue.found_page).filter(
        JobFieldValue.job_id == line["job_id"], JobFieldValue.label_name == label_name,
        JobFieldValue.job_document_id == line["job_document_id"])
    q = q.filter(JobFieldValue.row_index.is_(None)) if line["row_index"] is None \
        else q.filter(JobFieldValue.row_index == line["row_index"])
    row = q.first()
    return row[0] if row else None


# ---------------------------------------------------------------------------- AI Suggestion

SUGGEST_SYSTEM = (
    "You improve the instruction that tells an AI model how to read ONE field from shipping and "
    "customs documents (invoices, air waybills, bills of lading, packing lists). You are given "
    "the field's current instruction, its format hint, every value the model read for it next "
    "to the value a human operator corrected it to, and page images where the value appears.\n"
    "1. Work out WHY the model was wrong: the pattern behind the corrections (wrong column, "
    "dropped letters, wrong unit, value missed, a neighbouring field taken instead, formatting).\n"
    "2. Write an improved instruction that fixes that pattern and keeps everything in the current "
    "instruction that still holds. Be specific about where on the document to look and how to "
    "write the value. Describe the technique, never hard-code one job's value as the answer.\n"
    "3. If the format hint contributed to the errors, give a corrected one; otherwise repeat it.\n"
    "Where the operator's corrections disagree with each other or look like a different field, "
    "say so in the analysis rather than forcing them into the instruction.\n"
    'Reply with JSON only: {"analysis": "2-6 short sentences", "prompt": "the full new '
    'instruction", "format_hint": "the format hint"}'
)


def suggest(db: Session, group_id: str, template_document_id: str | None, label_name: str,
            prompt: str | None = None, format_hint: str | None = None) -> dict:
    """Never saves anything. `prompt` / `format_hint` default to the field's current ones."""
    from app.core.llm import _user_content, create_chat_completion_with_retry, reasoning_safe_chat_params
    from app.models.job import Job

    kind, obj = resolve(db, group_id, template_document_id, label_name)
    editable, reason = _editable(kind, obj)
    if not editable:
        raise HTTPException(status_code=400, detail=reason)
    cur_prompt, cur_hint = _current(kind, obj)
    prompt = cur_prompt if prompt is None else prompt
    format_hint = cur_hint if format_hint is None else format_hint
    lines = _corrected_lines(db, group_id, template_document_id, label_name)
    if not lines:
        raise HTTPException(status_code=400, detail="This field has no corrections to learn from.")

    pairs = [{"job": line["job_reference"],
              "line": line["row_index"], "ai_read": line["ai_value"] or "",
              "operator_saved": line["final_value"] or "", "type": line["kind"]}
             for line in lines[:MAX_PAIRS]]
    images: list[Path] = []
    tenant_id = None
    for line in _samples(lines):
        job = db.get(Job, line["job_id"])
        tenant_id = tenant_id or getattr(job, "tenant_id", None)
        fp = _found_page(db, line, label_name) if kind == "mark" else None
        for jd in _source_docs(db, kind, obj, line["job_id"], line["job_document_id"]):
            for p in _pages(jd, fp):
                if p not in images and len(images) < MAX_IMAGES:
                    images.append(p)
    per_line = bool(getattr(obj, "is_multi_value", False) if kind == "mark" else getattr(obj, "per_row", False))
    text = (f"FIELD: {label_name}\n"
            f"FIELD TYPE: {'read off one document' if kind == 'mark' else 'computed from the documents'}"
            f"{' - one value per line item' if per_line else ''}\n\n"
            f"CURRENT INSTRUCTION:\n{prompt or '(empty)'}\n\n"
            + (f"CURRENT FORMAT HINT:\n{format_hint or '(none)'}\n\n" if kind == "mark" else "")
            + (f"FORMAT RULE APPLIED AFTERWARDS (not editable here):\n{obj.tenant_format_prompt}\n\n"
               if kind == "mark" and getattr(obj, "tenant_format_prompt", None) else "")
            + f"CORRECTIONS ({len(lines)} in total, {len(pairs)} shown) - what the model read vs what the operator saved:\n"
            + json.dumps(pairs, ensure_ascii=False, indent=0))
    model, _ = extraction_model(db)
    resp = create_chat_completion_with_retry(
        timeout=180, tenant_id=tenant_id, model=model,
        messages=[{"role": "system", "content": SUGGEST_SYSTEM},
                  {"role": "user", "content": _user_content(text, images or None)}],
        response_format={"type": "json_object"},
        **reasoning_safe_chat_params(model, temperature=0.2, max_tokens=4000),
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except ValueError:
        data = {"analysis": "The model's reply was not in the expected form.", "prompt": raw}
    return {
        "analysis": str(data.get("analysis") or "").strip(),
        "prompt": str(data.get("prompt") or "").strip(),
        "format_hint": (str(data.get("format_hint") or "").strip() or None) if kind == "mark" else None,
        "model": model, "corrections_used": len(pairs), "images_used": len(images),
    }


# ---------------------------------------------------------------------------- Test

def _read_mark(db: Session, mark, prompt: str, format_hint: str | None, jd, tenant_id) -> dict:
    """{row_index or None: value} for one document, read the way run_extraction reads it."""
    from app.core import extraction as ex

    model, forced_images = extraction_model(db)
    multi = bool(getattr(mark, "is_multi_value", False))
    m = SimpleNamespace(**{c: getattr(mark, c, None) for c in (
        "label_name", "tenant_format_prompt", "anchor_variations", "semantic_description",
        "example_value", "detected_anchor")})
    m.extraction_prompt, m.value_format_hint = prompt, format_hint
    spec = ex.field_spec(m, inline_format=not multi)
    text = _doc_text(jd)
    images = _all_pages(jd)
    excel = getattr(jd, "source_kind", "pdf") == "excel"
    use_images = (forced_images and not excel) or (not text.strip() and not excel)
    vision_model = model if forced_images else None
    if not multi:
        got = (ex.extract_document_fields_from_images(images, [spec], model=vision_model, tenant_id=tenant_id)
               if use_images else ex.extract_document_fields(text, [spec], tenant_id=tenant_id))
        return {None: got.get(mark.label_name)}
    rows = (ex.extract_document_rows_from_images(images, [spec], model=vision_model, tenant_id=tenant_id)
            if use_images else ex.extract_document_rows(text, [spec], tenant_id=tenant_id))
    return {i + 1: r.get(mark.label_name) for i, r in enumerate(rows)}


def _read_custom(db: Session, cf, prompt: str, docs: list, tenant_id) -> dict:
    from app.core.llm import compute_custom_field

    text = "\n\n".join(f"=== {getattr(jd, 'original_name', None) or jd.id} ===\n{_doc_text(jd)}" for jd in docs)
    images = None if text.strip() else [p for jd in docs for p in _pages(jd)] or None
    return {None: compute_custom_field(prompt, text, tenant_id=tenant_id, field_label=cf.label_name,
                                       images=images)}


def same_value(a: str | None, b: str | None) -> bool:
    """Strict: what the operator saved, ignoring only case, spaces and number formatting
    (1,450.00 = 1450). Not the cross-document check's word overlap - "95670" vs "9AN5670" is a
    wrong answer here, not a near match."""
    import re

    ca, cb = "".join(str(a or "").split()).upper(), "".join(str(b or "").split()).upper()
    if ca == cb:
        return True
    plain = re.compile(r"^-?[\d,]*\.?\d+$")
    if plain.match(ca) and plain.match(cb):
        try:
            return float(ca.replace(",", "")) == float(cb.replace(",", ""))
        except ValueError:
            return False
    return False


def test(db_factory, group_id: str, template_document_id: str | None, label_name: str,
         prompt: str, format_hint: str | None) -> dict:
    """Read the field again with `prompt` on the latest corrected jobs. Read-only."""
    from app.models.job import Job

    db = db_factory()
    try:
        kind, obj = resolve(db, group_id, template_document_id, label_name)
        editable, reason = _editable(kind, obj)
        if not editable:
            raise HTTPException(status_code=400, detail=reason)
        if kind == "custom" and getattr(obj, "per_row", False):
            raise HTTPException(status_code=400, detail="Test is not available yet for a computed field with one value per line.")
        lines = _samples(_corrected_lines(db, group_id, template_document_id, label_name))
        if not lines:
            raise HTTPException(status_code=400, detail="No corrected job of this field still exists to test on.")
        # One read per document (a line-item field is read once for all its lines).
        work: dict[tuple, dict] = {}
        for line in lines:
            key = (line["job_id"], line["job_document_id"] if kind == "mark" else None)
            work.setdefault(key, {"lines": []})["lines"].append(line)
        jobs = {j.id: j for j in db.query(Job).filter(Job.id.in_({k[0] for k in work})).all()}
        obj_id = obj.id
    finally:
        db.close()

    def run(key):
        session = db_factory()
        try:
            from app.models.custom_field import CustomField
            from app.models.field_mark import FieldMark

            fresh = session.get(FieldMark if kind == "mark" else CustomField, obj_id)
            docs = _source_docs(session, kind, fresh, key[0], key[1])
            if not docs:
                return key, None, "The document is no longer on this job."
            tenant_id = jobs[key[0]].tenant_id if key[0] in jobs else None
            if kind == "mark":
                return key, _read_mark(session, fresh, prompt, format_hint, docs[0], tenant_id), None
            return key, _read_custom(session, fresh, prompt, docs, tenant_id), None
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt test failed for %s", key, exc_info=True)
            return key, None, f"Could not read: {exc}"[:200]
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=max(1, min(TEST_WORKERS, len(work)))) as pool:
        results = list(pool.map(run, list(work)))

    out = []
    for key, values, error in results:
        for line in work[key]["lines"]:
            new = None if values is None else values.get(line["row_index"] if kind == "mark" else None)
            target = line["final_value"] or ""
            out.append({
                "job_id": line["job_id"], "job_reference": line["job_reference"],
                "row_index": line["row_index"], "ai_value": line["ai_value"], "new_value": new,
                "corrected_value": target, "error": error,
                "old_matches": same_value(line["ai_value"], target),
                "new_matches": error is None and same_value(new, target),
            })
    out.sort(key=lambda r: (r["job_reference"], r["row_index"] or 0))
    return {"lines": out, "old_correct": sum(r["old_matches"] for r in out),
            "new_correct": sum(r["new_matches"] for r in out), "total": len(out)}


# ---------------------------------------------------------------------------- Apply / Revert

def _save(db: Session, kind: str, obj, prompt: str, format_hint: str | None) -> None:
    """Through the template's own update functions - the same code the template editor uses."""
    from app.api.v1.field_marks import edit_custom_field, edit_mark
    from app.schemas.onboarding import CustomFieldEdit, MarkEdit

    if kind == "mark":
        edit_mark(obj.id, MarkEdit(extraction_prompt=prompt, value_format_hint=format_hint), db, None)
    else:
        edit_custom_field(obj.id, CustomFieldEdit(ai_prompt=prompt), db, None)


def apply(db: Session, group_id: str, template_document_id: str | None, label_name: str,
          prompt: str, format_hint: str | None, user, action: str = "apply") -> dict:
    from app.models.prompt_change import PromptChange

    kind, obj = resolve(db, group_id, template_document_id, label_name)
    editable, reason = _editable(kind, obj)
    if not editable:
        raise HTTPException(status_code=400, detail=reason)
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="The new prompt is empty.")
    old_prompt, old_hint = _current(kind, obj)
    new_hint = ((format_hint or "").strip() or None) if kind == "mark" else None
    if prompt == (old_prompt or "").strip() and new_hint == ((old_hint or "").strip() or None):
        raise HTTPException(status_code=400, detail="Nothing changed: the prompt and format hint are the same as now.")
    db.add(PromptChange(
        tenant_id=getattr(obj, "tenant_id", None), group_id=group_id,
        mark_id=obj.id if kind == "mark" else None, custom_field_id=obj.id if kind == "custom" else None,
        label_name=label_name[:100], old_prompt=old_prompt, new_prompt=prompt,
        old_format_hint=old_hint, new_format_hint=new_hint, action=action,
        changed_by_id=getattr(user, "id", None),
        changed_by_name=(getattr(user, "full_name", None) or getattr(user, "email", None) or "")[:255] or None))
    _save(db, kind, obj, prompt, new_hint)          # commits, with the history row
    logger.info("prompt %s for %s %s (%s) by %s", action, kind, obj.id, label_name,
                getattr(user, "email", None))
    return info(db, group_id, template_document_id, label_name)


def revert(db: Session, change_id: str, group_id: str, template_document_id: str | None,
           label_name: str, user) -> dict:
    from app.models.prompt_change import PromptChange

    change = db.get(PromptChange, change_id)
    kind, obj = resolve(db, group_id, template_document_id, label_name)
    if change is None or obj.id not in (change.mark_id, change.custom_field_id):
        raise HTTPException(status_code=404, detail="That change is not part of this field's history.")
    return apply(db, group_id, template_document_id, label_name, change.old_prompt or "",
                 change.old_format_hint, user, action="revert")
