"""The IRN Pending link (frontend/src/pages/irn/IrnPendingListPage.tsx and
IrnPendingDetailPage.tsx): reachable by pasting its URL alone, no login screen - every route
here is the ONLY thing on the whole API that accepts a `key` query param instead of a session
cookie. Deliberately its own small module, not folded into jobs.py's own endpoints, so this
one narrow exception to "you must be logged in" stays easy to find, audit, and revoke.

Each TENANT has its own key (Tenant.irn_pending_access_key, generated from the Super Admin
dashboard's "Generate Link" button - see generate_irn_pending_key in tenants.py) rather than
one key shared by every tenant's jobs the way the very first version of this feature worked -
a key resolves to exactly one tenant, and every route here only ever sees that tenant's jobs.
Revoking one tenant's link (clearing its column) never affects any other tenant's.

Whoever holds a link acts as ashraf.ali@workboosterai.com (a real Super Admin account,
narrowed to the one tenant the key resolved to) for these reads, and for one write -
uploading a document's DSC-signed file + IRN number (sign_irn_pending_document below);
nothing else is reachable this way. That account is looked up fresh on every call, not
cached, so disabling it (User.is_active) revokes every tenant's link at once without
redeploying anything.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import TenantScope, get_db
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(prefix="/public/irn-pending", tags=["public irn pending"])

# The literal value seeded onto the "4S Logistics" tenant by migration c3f1tenantirnkey53 -
# that tenant already had a live link out in the world using this key (from before per-tenant
# keys existed), so it keeps working unchanged. Every OTHER tenant's key is freshly random
# (secrets.token_urlsafe(32), generated in tenants.py) - this literal is not special-cased
# anywhere in the lookup below, it is just data one tenant's row happens to hold.
PUBLIC_ACCESS_KEY = "PvWxx-1WQ8ZlCcmZpNFZGNxMYPsPZKKBPWdpVZ9mygY"

PUBLIC_ACCOUNT_EMAIL = "ashraf.ali@workboosterai.com"


def require_public_access(key: str | None = None, db: Session = Depends(get_db)) -> tuple[User, Tenant]:
    """Resolves BOTH who's acting (always the fixed ashraf super-admin account) and which
    tenant this particular key belongs to. A key that doesn't match any tenant's
    irn_pending_access_key is indistinguishable from a missing one - both are just "wrong
    key" to whoever's asking."""
    if not key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or wrong key")
    tenant = db.query(Tenant).filter(Tenant.irn_pending_access_key == key).first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or wrong key")
    user = db.query(User).filter(User.email == PUBLIC_ACCOUNT_EMAIL).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="The linked account is unavailable")
    return user, tenant


def _public_scope(tenant: Tenant) -> TenantScope:
    return TenantScope(tenant_id=tenant.id, is_super_admin=True)


def _irn_signature_dir(sig_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "irn_signatures" / sig_id


def _document_groups(db: Session, job) -> list[dict]:
    """Every document header on this job that the DSC + IRN Number screen shows - a captured
    job document (grouped by template_document_id, the same way JobRunPage's own docSlots
    groups them) or a Supporting Document (one header per row) - merged into one list, each
    with its unsigned file(s) and, once uploaded, its signed replacement + IRN number."""
    from app.api.v1.jobs import _build_detail
    from app.models.job_irn_signature import JobIrnSignature
    from app.models.supporting_document import SupportingDocument

    detail = _build_detail(db, job)
    groups: list[dict] = []
    seen: set[str] = set()
    for d in detail.documents:
        if not d.is_uploaded or d.template_document_id in seen:
            continue
        seen.add(d.template_document_id)
        files = [
            {"job_document_id": jd.id, "page_count": jd.page_count}
            for jd in detail.documents
            if jd.template_document_id == d.template_document_id and jd.is_uploaded
        ]
        groups.append({
            "doc_ref": d.template_document_id, "label": f"{d.name} ({d.doc_type})",
            "kind": "job_document", "unsigned_files": files,
        })

    rows = (
        db.query(SupportingDocument)
        .filter(SupportingDocument.job_id == job.id)
        .order_by(SupportingDocument.created_at)
        .all()
    )
    for row in rows:
        groups.append({
            "doc_ref": f"supporting-{row.id}", "label": row.label, "kind": "supporting_document",
            "unsigned_files": [
                {"stored_as": f.get("stored_as"), "original_name": f.get("original_name")}
                for f in (row.files or [])
            ],
        })

    sigs = {
        s.doc_ref: s for s in
        db.query(JobIrnSignature).filter(JobIrnSignature.job_id == job.id).all()
    }
    for g in groups:
        sig = sigs.get(g["doc_ref"])
        g["signed"] = None if sig is None else {
            "irn_number": sig.irn_number, "original_name": sig.original_name,
            "stored_as": sig.stored_as, "created_at": sig.created_at.isoformat(),
        }
    return groups


@router.get("")
def list_irn_pending(access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db)):
    from app.api.v1.jobs import _job_out
    from app.models.job import Job

    _user, tenant = access
    jobs = (
        db.query(Job)
        .filter(Job.tenant_id == tenant.id, Job.gk2_status == "irn_document_process")
        .order_by(Job.created_at.desc())
        .all()
    )
    return [_job_out(db, j) for j in jobs]


@router.get("/{job_id}")
def get_irn_pending_job(
    job_id: str, access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
):
    from app.api.v1.jobs import _build_detail, _load_job

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    return _build_detail(db, job)


@router.get("/{job_id}/supporting-documents")
def list_irn_pending_supporting_documents(
    job_id: str, access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
) -> dict:
    from app.api.v1.jobs import _load_job, _supporting_document_out
    from app.models.supporting_document import SupportingDocument

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    rows = (
        db.query(SupportingDocument)
        .filter(SupportingDocument.job_id == job.id)
        .order_by(SupportingDocument.created_at)
        .all()
    )
    return {"documents": [_supporting_document_out(r) for r in rows]}


@router.get("/{job_id}/documents/{job_document_id}/pages/{page_number}")
def get_irn_pending_document_page(
    job_id: str, job_document_id: str, page_number: int,
    access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
):
    """Same rendered-page image the authenticated preview uses (jobs.py's own
    get_job_document_page) - duplicated here rather than imported since that endpoint's auth
    dependency is baked into its signature, not separable from the file-serving logic."""
    from app.api.v1.jobs import _job_doc_dir, _load_job
    from app.models.job import JobDocument

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    jd = db.query(JobDocument).filter(JobDocument.id == job_document_id, JobDocument.job_id == job.id).first()
    if jd is None or jd.file_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job document not found")
    if page_number < 1 or page_number > jd.page_count:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page out of range")
    image = _job_doc_dir(jd.id) / "pages" / f"page_{page_number}.png"
    if not image.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered page missing")
    return FileResponse(image, media_type="image/png")


@router.get("/{job_id}/supporting-documents/{doc_id}/files/{stored_as}")
def get_irn_pending_supporting_document_file(
    job_id: str, doc_id: str, stored_as: str,
    access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
):
    from app.api.v1.jobs import _load_job, _supporting_doc_dir
    from app.models.supporting_document import SupportingDocument

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    row = db.query(SupportingDocument).filter(
        SupportingDocument.id == doc_id, SupportingDocument.job_id == job.id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    match = next((f for f in (row.files or []) if f.get("stored_as") == stored_as), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = _supporting_doc_dir(row.id) / stored_as
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing on disk")
    return FileResponse(path, filename=match.get("original_name") or stored_as)


@router.get("/{job_id}/documents")
def list_irn_pending_documents(
    job_id: str, access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
) -> dict:
    """Every document header this job's DSC + IRN Number screen needs - see _document_groups.
    Each entry carries enough to build BOTH thumbnails the UI shows side by side: its
    unsigned file(s) (served by the existing get_irn_pending_document_page /
    get_irn_pending_supporting_document_file routes above) on the left, and, once uploaded,
    its signed file (served by get_irn_pending_signed_file below) on the right."""
    from app.api.v1.jobs import _load_job

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    return {"documents": _document_groups(db, job)}


@router.post("/{job_id}/documents/{doc_ref}/sign")
def sign_irn_pending_document(
    job_id: str,
    doc_ref: str,
    irn_number: str = Form(...),
    file: UploadFile = File(...),
    access: tuple[User, Tenant] = Depends(require_public_access),
    db: Session = Depends(get_db),
) -> dict:
    """One document at a time: attach its DSC-signed file + IRN number. The caller never
    sends a status - once every header _document_groups lists for this job has a signed row,
    the job is moved on automatically (the exact same real-ERP-submission path GK2 pressing
    "Final Approve & Proceed" itself triggers - excel creation, then ERP entry, same as
    always), same as if GK1 had chosen Skip in the first place."""
    from app.api.v1.jobs import _kick_off_real_erp_submission, _load_job
    from app.models.job_irn_signature import JobIrnSignature

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    if job.gk2_status != "irn_document_process":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This job is not waiting on IRN document signing.",
        )

    groups = _document_groups(db, job)
    group = next((g for g in groups if g["doc_ref"] == doc_ref), None)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such document on this job")

    irn_number = irn_number.strip()
    if not irn_number:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="IRN number is required")

    existing = db.query(JobIrnSignature).filter(
        JobIrnSignature.job_id == job.id, JobIrnSignature.doc_ref == doc_ref).first()
    suffix = Path(file.filename or "").suffix
    stored_as = f"signed{suffix}"
    row = existing or JobIrnSignature(tenant_id=job.tenant_id, job_id=job.id, doc_ref=doc_ref)
    row.label = group["label"]
    row.irn_number = irn_number
    row.stored_as = stored_as
    row.original_name = file.filename or stored_as
    if existing is None:
        db.add(row)
        db.flush()  # assigns row.id, which the file's own folder is named after below

    ddir = _irn_signature_dir(row.id)
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / stored_as).write_bytes(file.file.read())
    db.commit()

    groups = _document_groups(db, job)
    all_signed = len(groups) > 0 and all(g["signed"] is not None for g in groups)
    if all_signed:
        _kick_off_real_erp_submission(db, job)
        db.refresh(job)

    return {"documents": groups, "job_status": job.gk2_status, "all_signed": all_signed}


@router.get("/{job_id}/documents/{doc_ref}/signed-file")
def get_irn_pending_signed_file(
    job_id: str, doc_ref: str,
    access: tuple[User, Tenant] = Depends(require_public_access), db: Session = Depends(get_db),
):
    from app.api.v1.jobs import _load_job
    from app.models.job_irn_signature import JobIrnSignature

    user, tenant = access
    scope = _public_scope(tenant)
    job = _load_job(db, job_id, scope, user)
    sig = db.query(JobIrnSignature).filter(
        JobIrnSignature.job_id == job.id, JobIrnSignature.doc_ref == doc_ref).first()
    if sig is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not signed yet")
    path = _irn_signature_dir(sig.id) / sig.stored_as
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File is missing on disk")
    return FileResponse(path, filename=sig.original_name)
