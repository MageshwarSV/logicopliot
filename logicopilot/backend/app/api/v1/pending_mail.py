"""Mail the auto-router could not place on its own: no customer's documents matched, or more
than one did. Held with its attachments (see email_puller._save_pending_email) so a person can
pick the template by hand instead of the mail simply being read once and forgotten.

Cross-tenant by nature — which tenant this even belongs to is exactly the thing nobody could
tell automatically — so this is Super Admin only, same as every other cross-tenant view.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.classifier import assign_documents_detailed
from app.core.config import get_settings
from app.core.deps import get_db, require_role
from app.core.email_puller import _document_name
from app.core.page_filter import extract_pdf_pages, kept_page_to_original
from app.models.job import Job, JobDocument
from app.models.pending_email import PendingEmail
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, User
from app.schemas.pending_email import PendingEmailOut, PendingEmailResolve

router = APIRouter(prefix="/pending-emails", tags=["pending emails"])


def _pending_dir(pending_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "pending_email" / pending_id


@router.get("", response_model=list[PendingEmailOut])
def list_pending_emails(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[PendingEmailOut]:
    rows = (
        db.query(PendingEmail)
        .filter(PendingEmail.status == "pending")
        .order_by(PendingEmail.created_at.desc())
        .all()
    )
    return [PendingEmailOut.from_row(r) for r in rows]


@router.get("/{pending_id}/attachments/{name}")
def get_pending_attachment(
    pending_id: str,
    name: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> FileResponse:
    row = db.get(PendingEmail, pending_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    match = next((a for a in (row.attachments or []) if a.get("path") == name), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    fpath = _pending_dir(pending_id) / name
    if not fpath.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File missing on disk")
    return FileResponse(fpath, filename=match.get("name") or name)


@router.post("/{pending_id}/resolve", response_model=PendingEmailOut)
def resolve_pending_email(
    pending_id: str,
    payload: PendingEmailResolve,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> PendingEmailOut:
    """Create the job under the template the person picked, and route the saved attachments
    into its slots with the SAME classifier the auto-pull and the operator's smart-upload
    use — so a document sorted by hand lands exactly where it would have if the router had
    only known which customer this was."""
    from app.api.v1.jobs import _job_doc_dir, _render_pages, generate_job_no

    row = db.get(PendingEmail, pending_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    group = db.get(TemplateGroup, payload.group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Template not found")

    pdir = _pending_dir(pending_id)
    prepared = []
    for a in (row.attachments or []):
        fpath = pdir / a["path"]
        if not fpath.exists():
            continue
        prepared.append({
            "name": a.get("name") or a["path"], "ext": a.get("ext") or "",
            "text": a.get("text") or "", "image": None, "blob": fpath.read_bytes(),
            "drop_pages": a.get("drop_pages") or [], "page_count": a.get("page_count") or 0,
        })
    if not prepared:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No attachments left to route")

    job = Job(
        # Every new job starts Unassigned, even one routed from a pending pull - assignment
        # is now always a deliberate action from the Jobs list' own dropdown, never an
        # automatic side effect of which mailbox a customer's mail happened to route to.
        tenant_id=group.tenant_id, group_id=group.id, reference=generate_job_no(),
        status="draft", created_by_id=user.id, assigned_operator_id=None,
    )
    db.add(job)
    db.flush()

    slots: dict[str, JobDocument] = {}
    slot_meta: dict[str, dict] = {}
    for tdoc in group.documents:
        jd = JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                         template_document_id=tdoc.id, page_count=0)
        db.add(jd)
        slots[tdoc.id] = jd
        slot_meta[tdoc.id] = {
            "key": tdoc.id, "name": tdoc.name, "doc_type": tdoc.doc_type,
            "fields": [m.label_name for m in tdoc.marks],
        }
    db.flush()

    claims_per_file = assign_documents_detailed(prepared, list(slot_meta.values()))
    for item, claims in zip(prepared, claims_per_file):
        # A combined attachment (one PDF carrying both the Invoice and the Packing List) must
        # give each slot only ITS pages, not the whole thing.
        split = len(claims) > 1 and item["ext"] == ".pdf"
        kept_original = (
            kept_page_to_original(item["page_count"], item["drop_pages"]) if split else []
        )
        for claim in claims:
            key = claim["key"]
            jd = slots.get(key)
            if jd is None:
                continue
            if jd.file_path:
                jd = JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                                 template_document_id=key, page_count=0,
                                 file_index=jd.file_index + 1)
                db.add(jd)
                db.flush()
                slots[key] = jd
            jd.original_name = (item["name"] or "")[:255] or None
            meta = slot_meta[key]
            fname = _document_name(meta["doc_type"], meta["name"], item["text"], item["ext"])
            ddir = _job_doc_dir(jd.id)
            ddir.mkdir(parents=True, exist_ok=True)
            dorig = ddir / fname
            if split:
                orig_pages = sorted({
                    kept_original[p - 1] for p in claim["pages"]
                    if 1 <= p <= len(kept_original)
                }) or kept_original
                dorig.write_bytes(extract_pdf_pages(item["blob"], orig_pages))
                jd.page_count = _render_pages(dorig, ddir / "pages")
            else:
                dorig.write_bytes(item["blob"])
                jd.page_count = _render_pages(dorig, ddir / "pages", skip=item.get("drop_pages"))
            jd.file_path = str(dorig)

    row.status = "resolved"
    row.resolved_group_id = group.id
    row.resolved_job_id = job.id
    row.resolved_by_id = user.id
    db.commit()
    db.refresh(row)

    # Kept for GK1's IRN Documents Upload "Prealert" view, exactly like an auto-pulled job —
    # this queue never held the raw MIME message, only the attachments, so only those survive.
    from app.core.job_email import save_original_email

    save_original_email(
        job.id, None, [(p["name"], p["blob"]) for p in prepared], row.sender, row.subject)

    # Clean up the holding copy now it has been copied into the job's own folder — same as a
    # normal pull, nothing of the mail is left lying around once it has somewhere real to live.
    import shutil
    shutil.rmtree(pdir, ignore_errors=True)

    # Extract whatever was actually attached, complete or not — the same rule the auto-pull
    # itself follows (run_extraction right after routing), rather than _maybe_auto_extract's
    # "only once every slot is filled", which is right for an operator mid-upload but would
    # leave a partially-attached resolved email sitting on Document Capture for no reason.
    from app.api.v1.jobs import _begin_extraction

    _begin_extraction(db, job)

    return PendingEmailOut.from_row(row)


@router.post("/{pending_id}/dismiss", response_model=PendingEmailOut)
def dismiss_pending_email(
    pending_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> PendingEmailOut:
    row = db.get(PendingEmail, pending_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    row.status = "dismissed"
    row.resolved_by_id = user.id
    db.commit()
    db.refresh(row)
    import shutil
    shutil.rmtree(_pending_dir(pending_id), ignore_errors=True)
    return PendingEmailOut.from_row(row)
