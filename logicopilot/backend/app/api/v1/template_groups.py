import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user, get_db, require_role
from app.core.modes import MODES
from app.models.cross_doc_link import CrossDocLink
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User
logger = logging.getLogger(__name__)

from app.schemas.onboarding import (
    CrossDocLinkOut,
    TemplateGroupCreate,
    TemplateGroupDetailOut,
    TemplateGroupOut,
)

router = APIRouter(prefix="/template-groups", tags=["onboarding: groups"])


def _detail(db: Session, group: TemplateGroup) -> TemplateGroupDetailOut:
    """A template set with everything hanging off it.

    Custom fields and cross-document links are queried by group id rather than walked from a
    relationship, so they have to be attached here. Every route that returns a full template
    set goes through this - a route that builds the response itself will quietly answer with
    them missing.
    """
    from app.schemas.onboarding import CustomFieldOut

    detail = TemplateGroupDetailOut.model_validate(group)
    detail.cross_doc_links = [
        CrossDocLinkOut.model_validate(c)
        for c in db.query(CrossDocLink).filter(CrossDocLink.group_id == group.id).all()
    ]
    detail.custom_fields = [
        CustomFieldOut.model_validate(c)
        for c in db.query(CustomField).filter(CustomField.group_id == group.id).all()
    ]
    return detail


@router.post("", response_model=TemplateGroupDetailOut, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: TemplateGroupCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupDetailOut:
    """Step 1: create the group and one empty document row per declared name."""
    tenant = db.get(Tenant, payload.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    if payload.mode not in MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a valid mode.")
    if tenant.allowed_modes and payload.mode not in tenant.allowed_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"{tenant.name} is not licensed for {payload.mode}. "
                   f"Allowed: {', '.join(tenant.allowed_modes)}"),
        )

    group = TemplateGroup(tenant_id=payload.tenant_id, name=payload.name, status="draft",
                          mode=payload.mode)
    db.add(group)
    db.flush()  # assign group.id before creating children

    for index, doc in enumerate(payload.documents):
        db.add(
            TemplateDocument(
                tenant_id=payload.tenant_id,
                group_id=group.id,
                name=doc.name,
                doc_type=doc.doc_type,
                order_index=index,
                is_required=doc.is_required,
            )
        )
    db.commit()
    db.refresh(group)
    return _detail(db, group)


@router.get("", response_model=list[TemplateGroupOut])
def list_groups(
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[TemplateGroupOut]:
    query = db.query(TemplateGroup)
    if tenant_id:
        query = query.filter(TemplateGroup.tenant_id == tenant_id)
    groups = query.order_by(TemplateGroup.created_at.desc()).all()
    return [TemplateGroupOut.model_validate(g) for g in groups]


@router.get("/{group_id}", response_model=TemplateGroupDetailOut)
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> TemplateGroupDetailOut:
    """Full template state: documents + their marks + cross-doc links.
    Super admins see any; tenant admins only their own tenant's."""
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if user.role == TENANT_ADMIN and group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")

    return _detail(db, group)


# --------------------------------------------------------------------------- #
# Entry mode - how this customer's data reaches the ERP
# --------------------------------------------------------------------------- #
def _excel_dir(group_id: str) -> Path:
    """Where a group's own import template lives. One file per group, overwritten on re-upload."""
    return Path(get_settings().uploads_dir) / "excel_templates" / group_id


class EntryModePayload(BaseModel):
    # fields — the recorded script types every value into its own box
    # excel  — the job becomes one workbook and the script attaches it to a bulk import
    entry_mode: str
    excel_config: dict | None = None


@router.post("/{group_id}/excel-template")
def upload_excel_template(
    group_id: str,
    file: UploadFile = File(...),
    sheet: str | None = None,
    header_row: int = 1,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Take the customer's own import workbook and report the columns it expects.

    The headings are what the ERP matches on, so they are read from the file rather than typed
    in again - a heading that differs by one space is a column the ERP will not recognise.
    Returns the sheet names, the headings in order and their column letters, so the next screen
    can ask which data field feeds each one.
    """
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    name = (file.filename or "").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422,
                            detail="Upload an .xlsx or .xlsm workbook (an old .xls cannot be read).")
    content = file.file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The workbook exceeds the 20 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")

    ddir = _excel_dir(group_id)
    ddir.mkdir(parents=True, exist_ok=True)
    # One workbook per group. Uploading a .xlsx after a .xlsm used to leave BOTH on disk, and
    # the job run picked whichever the glob sorted first - so a re-upload could silently keep
    # using the old file.
    for stale in ddir.glob("template.*"):
        try:
            stale.unlink()
        except OSError:
            logger.warning("could not remove the previous workbook %s", stale)
    saved = ddir / ("template" + Path(name).suffix.lower())
    saved.write_bytes(content)

    from app.core.excel_entry import read_headers

    try:
        info = read_headers(saved, sheet=sheet, header_row=header_row)
    except RuntimeError as exc:      # openpyxl not installed on the server
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f"That workbook could not be read: {exc}") from exc
    return {**info, "file_name": name, "stored": str(saved)}


class SheetPick(BaseModel):
    # Which sheet inside the already-uploaded workbook, and which row holds the headings.
    sheet: str | None = None
    header_row: int = 1


@router.post("/{group_id}/excel-template/headers")
def read_excel_template_headers(
    group_id: str,
    body: SheetPick,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Re-read the STORED workbook for a different sheet or header row.

    A real ERP import template usually has several sheets - instructions, lookups, and the one
    that actually gets imported - and the headings are often not on row 1 because there is a
    title or a note above them. Picking either has to re-read the file, and making the user
    upload it again for that would be absurd, so this reads the copy already on disk.
    """
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    stored = next(iter(sorted(_excel_dir(group_id).glob("template.*"))), None)
    if stored is None:
        raise HTTPException(
            status_code=422,
            detail="No workbook has been uploaded for this template set yet.")
    if body.header_row < 1 or body.header_row > 1000:
        raise HTTPException(status_code=422, detail="The header row must be between 1 and 1000.")

    from app.core.excel_entry import read_headers

    try:
        info = read_headers(stored, sheet=body.sheet, header_row=body.header_row)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f"That workbook could not be read: {exc}") from exc
    if body.sheet and body.sheet not in (info.get("sheets") or []):
        raise HTTPException(
            status_code=422,
            detail=f"The workbook has no sheet called {body.sheet!r}.")
    if not any((h.get("header") or "").strip() for h in info.get("headers") or []):
        # Not an error - a title row looks exactly like this - but the UI must be able to say so
        # rather than showing an empty mapping table with no explanation.
        info["warning"] = (
            f"Row {body.header_row} of {info.get('sheet')!r} has no headings on it. "
            "If the sheet has a title above the headings, try the next row down."
        )
    return {**info, "stored": str(stored)}


@router.patch("/{group_id}/entry-mode", response_model=TemplateGroupDetailOut)
def set_entry_mode(
    group_id: str,
    payload: EntryModePayload,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupDetailOut:
    """Choose field-by-field or Excel entry, and save the column mapping for Excel.

    Validated rather than trusted: an Excel mode with nothing mapped would produce an empty
    sheet at run time, which the operator would only discover when the ERP rejected it.
    """
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    mode = (payload.entry_mode or "fields").strip().lower()
    if mode not in ("fields", "excel"):
        raise HTTPException(status_code=422, detail="entry_mode must be 'fields' or 'excel'.")
    if mode == "excel":
        cfg = payload.excel_config or {}
        if (cfg.get("source") or "blank") not in ("blank", "template"):
            raise HTTPException(status_code=422, detail="source must be 'blank' or 'template'.")
        # Validate through the same normaliser the writer uses, so a config that saves is a
        # config that can be built. A real ERP template fills SEVERAL sheets at different row
        # counts, so `sheets` is the shape now - the old single-sheet form still normalises.
        from app.core.excel_entry import SERIALS, _literals, sheet_plans

        plans = sheet_plans(cfg)
        if not plans:
            raise HTTPException(
                status_code=422,
                detail="Configure at least one sheet, or there would be nothing to import.")
        mapped = 0
        for plan in plans:
            if not plan["sheet"]:
                raise HTTPException(status_code=422,
                                    detail="Every configured sheet needs a sheet name.")
            if plan["header_row"] < 1:
                raise HTTPException(
                    status_code=422,
                    detail=f"The header row for {plan['sheet']!r} must be 1 or more.")
            for col in plan["columns"]:
                field = ((col or {}).get("field") or "").strip()
                if not field:
                    # A column can instead carry a literal - a constant the import always
                    # needs ('KGS', '0.00'), or one value per row on a fixed block like
                    # STATEMENT. That is mapped too: it puts data in the file.
                    if _literals(col or {}) is not None:
                        mapped += 1
                    continue
                mapped += 1
                if field.startswith("#") and field not in SERIALS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{field!r} is not a generated column. Use one of: "
                               + ", ".join(sorted(SERIALS)))
        if not mapped:
            raise HTTPException(
                status_code=422,
                detail="Map at least one column to a data field, or the workbook would be empty.")
        if cfg.get("source") == "template":
            if not next(iter(_excel_dir(group_id).glob("template.*")), None):
                raise HTTPException(
                    status_code=422,
                    detail="No workbook has been uploaded for this template set yet.")
            # Each sheet named in the config has to exist in the uploaded workbook, or the run
            # would fail later with the operator holding the job.
            from app.core.excel_entry import read_headers

            stored = next(iter(_excel_dir(group_id).glob("template.*")))
            try:
                available = read_headers(stored).get("sheets") or []
            except Exception:  # noqa: BLE001
                available = []
            missing = [p["sheet"] for p in plans if available and p["sheet"] not in available]
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=f"The uploaded workbook has no sheet called {missing[0]!r} "
                           f"(it has {', '.join(available)}).")
        group.excel_config = cfg
    group.entry_mode = mode
    db.commit()
    db.refresh(group)
    return get_group(group_id, db, _)  # type: ignore[arg-type]


class RulingPayload(BaseModel):
    ruling_prompt: str | None = None


@router.patch("/{group_id}/ruling", response_model=TemplateGroupDetailOut)
def set_ruling(
    group_id: str,
    payload: RulingPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupDetailOut:
    """Set (or clear) the custom ruling that decides which documents a job requires."""
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    group.ruling_prompt = (payload.ruling_prompt or "").strip() or None
    db.commit()
    return get_group(group_id, db, user)  # type: ignore[arg-type]


class PullEmailPayload(BaseModel):
    pull_email: str | None = None
    # The operator who should own (and exclusively see) this customer's mail-pulled jobs.
    # Use the sentinel "unset" to leave unchanged; None/"" to clear.
    pull_operator_id: str | None = "unset"


class RenamePayload(BaseModel):
    name: str


@router.patch("/{group_id}/name", response_model=TemplateGroupOut)
def rename_group(
    group_id: str,
    payload: RenamePayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> TemplateGroupOut:
    """Rename a template set.

    Only the name changes. Every job, mark, script and assignment references the group by id,
    so nothing has to be migrated and job history keeps whatever it recorded at the time — a
    template built as a working shorthand can be given the customer's real name later without
    disturbing the work already done under it.
    """
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty")
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if user.role == TENANT_ADMIN and group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    group.name = name[:200]
    db.commit()
    db.refresh(group)
    return TemplateGroupOut.model_validate(group)


class ModePayload(BaseModel):
    mode: str


@router.patch("/{group_id}/mode", response_model=TemplateGroupOut)
def set_group_mode(
    group_id: str,
    payload: ModePayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> TemplateGroupOut:
    """Change which transport mode a template is tagged for — for a template built before
    this existed, or one whose name turned out not to match what it actually processes
    (an "air export" template with no air waybill on any of its documents is a Sea Export
    template that was named wrong)."""
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if user.role == TENANT_ADMIN and group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if payload.mode not in MODES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a valid mode.")
    tenant = db.get(Tenant, group.tenant_id)
    if tenant is not None and tenant.allowed_modes and payload.mode not in tenant.allowed_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"{tenant.name} is not licensed for {payload.mode}. "
                   f"Allowed: {', '.join(tenant.allowed_modes)}"),
        )
    group.mode = payload.mode
    db.commit()
    db.refresh(group)
    return TemplateGroupOut.model_validate(group)


@router.patch("/{group_id}/pull-email", response_model=TemplateGroupOut)
def set_pull_email(
    group_id: str,
    payload: PullEmailPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> TemplateGroupOut:
    """Assign (or clear) the mailbox address that feeds this customer/template, and
    optionally the operator who owns the jobs it produces."""
    from app.models.user import OPERATOR, User as UserModel

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if user.role == TENANT_ADMIN and group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    group.pull_email = (payload.pull_email or "").strip() or None
    if payload.pull_operator_id != "unset":
        op_id = payload.pull_operator_id or None
        if op_id is not None:
            op = db.get(UserModel, op_id)
            if op is None or op.role != OPERATOR or op.tenant_id != group.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Chosen operator must belong to this tenant.",
                )
        group.pull_operator_id = op_id
    db.commit()
    db.refresh(group)
    return TemplateGroupOut.model_validate(group)


class GroupTenantPayload(BaseModel):
    tenant_id: str


@router.patch("/{group_id}/tenant", response_model=TemplateGroupDetailOut)
def set_group_tenant(
    group_id: str,
    payload: GroupTenantPayload,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupDetailOut:
    """Move a template set to a DIFFERENT tenant - for one duplicated or built before its real
    owner was settled, or one that simply needs to move. Super-Admin only: unlike /mode and
    /name (which a Tenant Admin may also use on their own tenant's template), this changes
    WHICH tenant the template belongs to, a platform-level action.

    Refused outright once the group has any real job against it - see delete_group's own
    precedent just above (it deletes every job under a group outright on template deletion):
    a group with job history is real usage, not mutable configuration, and
    `Job`/`JobDocument`/`JobFieldValue`/`JobEvent` are all tenant-scoped too - cascading this
    change into them would leave that history permanently inconsistent. Duplicate the
    template into the new tenant instead (POST .../duplicate, above) - a fresh copy with no
    job history to protect.

    Every other tenant_id this template's own configuration carries - TemplateDocument,
    FieldMark, CustomField, CrossDocLink, TemplateReview, DocumentSample (classifier training
    excerpts, keyed through template_documents), CompositeFieldConsigneeDefault (per-
    consignee field ordering, keyed through custom_fields) - moves with it. The one exception
    is UserTemplateAssignment: deleted rather than moved, since the assigned user still
    belongs to the OLD tenant and has no business being auto-assigned to a different
    tenant's template - the same reasoning duplicate_group already applies by never copying
    this table at all.
    """
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    new_tenant_id = payload.tenant_id
    old_tenant_id = group.tenant_id
    if new_tenant_id == old_tenant_id:
        return _detail(db, group)

    new_tenant = db.get(Tenant, new_tenant_id)
    if new_tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination tenant not found")

    job_count = db.execute(text("SELECT COUNT(*) FROM jobs WHERE group_id=:g"), {"g": group_id}).scalar()
    if job_count:
        raise HTTPException(
            status_code=409,
            detail=(f"This template already has {job_count} job(s) against it - reassigning "
                   f"its tenant would leave that job history inconsistent. Duplicate it into "
                   f"the new tenant instead, which starts fresh with no job history to protect."),
        )

    if new_tenant.allowed_modes and group.mode not in new_tenant.allowed_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"{new_tenant.name} is not licensed for {group.mode}. "
                   f"Allowed: {', '.join(new_tenant.allowed_modes)}"),
        )

    clash = (
        db.query(TemplateGroup)
        .filter(TemplateGroup.tenant_id == new_tenant_id, TemplateGroup.name == group.name)
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{new_tenant.name} already has a template set called {group.name!r}. Rename this one first.",
        )

    p = {"g": group_id, "t": new_tenant_id}
    db.execute(text("UPDATE template_documents SET tenant_id=:t WHERE group_id=:g"), p)
    db.execute(text(
        "UPDATE field_marks SET tenant_id=:t "
        "WHERE document_id IN (SELECT id FROM template_documents WHERE group_id=:g)"
    ), p)
    db.execute(text("UPDATE custom_fields SET tenant_id=:t WHERE group_id=:g"), p)
    db.execute(text("UPDATE cross_doc_links SET tenant_id=:t WHERE group_id=:g"), p)
    db.execute(text("UPDATE template_reviews SET tenant_id=:t WHERE group_id=:g"), p)
    db.execute(text(
        "UPDATE document_samples SET tenant_id=:t "
        "WHERE template_document_id IN (SELECT id FROM template_documents WHERE group_id=:g)"
    ), p)
    db.execute(text(
        "UPDATE composite_field_consignee_defaults SET tenant_id=:t "
        "WHERE custom_field_id IN (SELECT id FROM custom_fields WHERE group_id=:g)"
    ), p)
    db.execute(text("DELETE FROM user_template_assignments WHERE group_id=:g"), p)

    group.tenant_id = new_tenant_id
    db.commit()
    db.refresh(group)
    logger.info("moved template set %s (%s) from tenant %s to %s",
                group.name, group_id, old_tenant_id, new_tenant_id)
    return _detail(db, group)


@router.post("/{group_id}/finalize", response_model=TemplateGroupOut)
def finalize_group(
    group_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupOut:
    """Mark the template set ready — this is what makes it runnable as a Job."""
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    group.status = "ready"
    db.commit()
    db.refresh(group)
    return TemplateGroupOut.model_validate(group)


class DuplicatePayload(BaseModel):
    # The copy's name. Left to the caller on purpose: "nokia(excel)" says what it is for far
    # better than "Nokia (copy)" would.
    name: str
    # None (the default) = same tenant as the source group, byte-identical to this endpoint's
    # only behavior before this field existed. A real id here is a cross-tenant copy - a
    # brand-new customer whose paperwork is close to one already trained, starting from that
    # trained template instead of from scratch.
    destination_tenant_id: str | None = None


@router.post("/{group_id}/duplicate", response_model=TemplateGroupDetailOut,
             status_code=status.HTTP_201_CREATED)
def duplicate_group(
    group_id: str,
    payload: DuplicatePayload,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> TemplateGroupDetailOut:
    """Copy a whole template set - documents, sample files, marks, custom fields, links.

    Used two ways: (1) one customer needs a second variant of the same paperwork - the same
    documents and the same extraction, entered into the ERP a different way (typed field by
    field on one, imported as a workbook on the other); (2) a BRAND-NEW customer's paperwork
    is close to one already trained, so their own template starts from that one instead of
    from scratch - `destination_tenant_id` names the new tenant and the copy becomes a fully
    independent template of theirs; nothing further done to either copy ever touches the other.

    Deliberately NEVER copied, same-tenant or not: the email address that routes incoming
    mail and the operator it is routed to (a copy stealing the original's incoming mail, or
    pointing at a user who may not even belong to the destination tenant, would be worse than
    leaving it blank); the per-group material-master workbook (the source customer's own
    real product/HS-code data, not template configuration); `ErpScript` rows (tenant-level ERP
    automation the destination tenant sets up for itself); `CustomFieldReferenceValue` rows
    (lookup answers LEARNED from the source tenant's own real documents, meaningless - or
    worse, wrong - for an unrelated company's documents).
    """
    src = db.get(TemplateGroup, group_id)
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the copy a name.")

    dest_tenant_id = payload.destination_tenant_id or src.tenant_id
    cross_tenant = dest_tenant_id != src.tenant_id
    if cross_tenant:
        dest_tenant = db.get(Tenant, dest_tenant_id)
        if dest_tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination tenant not found")
        # Same licensing check create_group already applies when building a fresh group for
        # this tenant - a destination not licensed for this mode must refuse a copy of it too.
        if dest_tenant.allowed_modes and src.mode not in dest_tenant.allowed_modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(f"{dest_tenant.name} is not licensed for {src.mode}. "
                       f"Allowed: {', '.join(dest_tenant.allowed_modes)}"),
            )

    clash = (
        db.query(TemplateGroup)
        .filter(TemplateGroup.tenant_id == dest_tenant_id, TemplateGroup.name == name)
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This customer already has a template set called {name!r}. Pick another name.",
        )

    copy = TemplateGroup(
        tenant_id=dest_tenant_id,
        name=name,
        status=src.status,
        mode=src.mode,
        ruling_prompt=src.ruling_prompt,
        entry_mode=src.entry_mode,
        excel_config=src.excel_config,
        # pull_email/pull_operator_id are NOT copied - see this function's own docstring.
    )
    db.add(copy)
    db.flush()

    # Documents, with their sample file and rendered pages. Copying the files means deleting the
    # original later cannot blank out the copy's previews.
    from app.api.v1.template_documents import document_dir

    mark_ids: dict[str, str] = {}
    doc_ids: dict[str, str] = {}
    for doc in sorted(src.documents, key=lambda d: d.order_index):
        new_doc = TemplateDocument(
            tenant_id=copy.tenant_id,
            group_id=copy.id,
            name=doc.name,
            doc_type=doc.doc_type,
            order_index=doc.order_index,
            page_count=doc.page_count,
            is_required=doc.is_required,
        )
        db.add(new_doc)
        db.flush()
        doc_ids[doc.id] = new_doc.id
        if doc.file_path:
            old_dir, new_dir = document_dir(doc.id), document_dir(new_doc.id)
            if old_dir.exists():
                try:
                    shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)
                    new_doc.file_path = str(new_dir / Path(doc.file_path).name)
                except OSError:
                    # A copy without its sample file is still usable for live jobs; say so in
                    # the log rather than failing the whole duplicate.
                    logger.exception("could not copy the sample file for document %s", doc.id)
                    new_doc.page_count = 0
            else:
                logger.warning("document %s claims a file at %s but the folder is gone",
                               doc.id, doc.file_path)
                new_doc.page_count = 0

        for mark in doc.marks:
            new_mark = FieldMark(
                tenant_id=copy.tenant_id,
                document_id=new_doc.id,
                label_name=mark.label_name,
                page_number=mark.page_number,
                x=mark.x, y=mark.y, width=mark.width, height=mark.height,
                color=mark.color,
                detected_anchor=mark.detected_anchor,
                example_value=mark.example_value,
                anchor_variations=list(mark.anchor_variations or []) or None,
                semantic_description=mark.semantic_description,
                value_format_hint=mark.value_format_hint,
                extraction_prompt=mark.extraction_prompt,
                correction_prompt=mark.correction_prompt,
                tenant_format_prompt=mark.tenant_format_prompt,
                verify_with_other_document=mark.verify_with_other_document,
                ask_operator=mark.ask_operator,
                ask_operator_hint=mark.ask_operator_hint,
                ask_operator_required=mark.ask_operator_required,
                is_multi_value=mark.is_multi_value,
                standalone_multi_value=mark.standalone_multi_value,
                standalone_group_heading=mark.standalone_group_heading,
                is_target_value=mark.is_target_value,
                fuzzy_match=mark.fuzzy_match,
            )
            db.add(new_mark)
            db.flush()
            mark_ids[mark.id] = new_mark.id

    # Two passes: paired_custom_field_id/sync_field_ids point at OTHER custom_fields.id values,
    # which do not exist yet while the first one is still being created - same reason
    # source_document_ids above needs doc_ids built first. Pass 1 creates every field (every
    # column that does NOT reference another custom field); pass 2 patches the two that do,
    # once cf_ids maps every old id to its new one.
    src_custom_fields = db.query(CustomField).filter(CustomField.group_id == src.id).all()
    cf_ids: dict[str, str] = {}
    for cf in src_custom_fields:
        new_cf = CustomField(
            tenant_id=copy.tenant_id,
            group_id=copy.id,
            label_name=cf.label_name,
            kind=cf.kind,
            # Literal data typed in for the SOURCE customer (a GST number, a fixed consignee
            # name) - template CONFIGURATION this is not, so it must never leak into an
            # unrelated company's copy. Same-tenant duplicate (today's only case before this
            # function took a destination tenant) keeps it exactly as before.
            hardcoded_value=cf.hardcoded_value if not cross_tenant else None,
            ai_prompt=cf.ai_prompt,
            # The ids point at the ORIGINAL's documents; without remapping the AI would be fed
            # the wrong template set's files.
            source_document_ids=[doc_ids[d] for d in (cf.source_document_ids or [])
                                if d in doc_ids] or None,
            multi_value_from_document=cf.multi_value_from_document,
            ask_operator=cf.ask_operator,
            ask_operator_hint=cf.ask_operator_hint,
            ask_operator_required=cf.ask_operator_required,
            per_row=cf.per_row,
            lookup_key_label=cf.lookup_key_label,
            lookup_match_columns=list(cf.lookup_match_columns or []) or None,
            lookup_return_column=cf.lookup_return_column,
            verify_with_other_document=cf.verify_with_other_document,
            is_target_value=cf.is_target_value,
            fuzzy_match=cf.fuzzy_match,
            example_value=cf.example_value,
            picker_heading=cf.picker_heading,
            composite_source_labels=list(cf.composite_source_labels or []) or None,
        )
        db.add(new_cf)
        db.flush()
        cf_ids[cf.id] = new_cf.id

    for cf in src_custom_fields:
        if not cf.paired_custom_field_id and not cf.sync_field_ids:
            continue
        new_cf = db.get(CustomField, cf_ids[cf.id])
        new_cf.paired_custom_field_id = cf_ids.get(cf.paired_custom_field_id)
        new_cf.sync_field_ids = [cf_ids[i] for i in (cf.sync_field_ids or []) if i in cf_ids] or None

    for link in db.query(CrossDocLink).filter(CrossDocLink.group_id == src.id).all():
        # Exactly one of these is ever set on a real link (see CrossDocLink's own check
        # constraint) - resolve whichever it is through the matching id map, same as the mark
        # side below; a link whose source can't be resolved (its mark/field was somehow not
        # copied) is dropped rather than left pointing at nothing.
        if link.source_mark_id is not None:
            if link.source_mark_id not in mark_ids:
                continue
            new_source_mark_id, new_source_custom_field_id = mark_ids[link.source_mark_id], None
        else:
            if link.source_custom_field_id not in cf_ids:
                continue
            new_source_mark_id, new_source_custom_field_id = None, cf_ids[link.source_custom_field_id]
        if link.target_mark_id not in mark_ids:
            continue
        db.add(CrossDocLink(
            tenant_id=copy.tenant_id,
            group_id=copy.id,
            source_mark_id=new_source_mark_id,
            source_custom_field_id=new_source_custom_field_id,
            target_mark_id=mark_ids[link.target_mark_id],
            condition=link.condition,
        ))

    # The import workbook, if the original had one - so the copy's Excel step opens with the
    # sheets already readable instead of asking for the file again.
    src_xl, copy_xl = _excel_dir(src.id), _excel_dir(copy.id)
    if src_xl.exists():
        try:
            shutil.copytree(src_xl, copy_xl, dirs_exist_ok=True)
        except OSError:
            logger.exception("could not copy the import workbook of group %s", src.id)

    db.commit()
    db.refresh(copy)
    logger.info("duplicated template set %s into %s (%s documents, %s marks)",
                src.name, name, len(doc_ids), len(mark_ids))
    return _detail(db, copy)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")

    # Clean up everything tied to this template (SQLite FK cascade isn't enforced by
    # default, so remove dependents explicitly to avoid orphans).
    p = {"g": group_id}
    db.execute(text("DELETE FROM job_field_values WHERE job_id IN (SELECT id FROM jobs WHERE group_id=:g)"), p)
    db.execute(text("DELETE FROM job_documents WHERE job_id IN (SELECT id FROM jobs WHERE group_id=:g)"), p)
    db.execute(text("DELETE FROM jobs WHERE group_id=:g"), p)
    db.execute(text("DELETE FROM cross_doc_links WHERE group_id=:g"), p)
    db.execute(text("DELETE FROM user_template_assignments WHERE group_id=:g"), p)
    db.execute(text("DELETE FROM template_reviews WHERE group_id=:g"), p)
    db.execute(text("DELETE FROM custom_fields WHERE group_id=:g"), p)
    db.delete(group)  # ORM cascade removes its documents -> field marks
    db.commit()


@router.post("/{group_id}/material-master")
def upload_material_master(
    group_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Attach this customer's material master - their part code against its CTH.

    The classification of a part is settled long before any shipment and appears on none of the
    shipping documents, so without this list an operator types the CTH on every line of every
    job. The customer already exports it from their own system; it is taken as it comes, and
    the two columns that matter are found by their headings.
    """
    from app.core.material_master import (
        clear_cache, master_path, remember_name, start_background_parse,
    )

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Template group not found")
    name = (file.filename or "").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=422,
            detail="Upload an .xlsx or .xlsm workbook (an old .xls cannot be read).")
    content = file.file.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The file exceeds the 30 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")

    path = master_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One master per group: a second upload replaces the first rather than sitting beside it,
    # or a job could read whichever the glob happened to sort first.
    for stale in path.parent.glob(f"{group_id}.*"):
        try:
            stale.unlink()
        except OSError:
            logger.warning("could not remove the previous material master %s", stale)
    target = path.with_suffix(Path(name).suffix.lower())
    target.write_bytes(content)
    remember_name(group_id, name)
    # The parsed copy of the previous file must go, or every job would keep using the old list
    # until the process restarted.
    clear_cache(group_id)

    # NOT parsed here: a real master (9.8 MB / 22,000 rows) takes 25-30+ seconds, and doing
    # that inline in this request handler was observed to get the worker serving it killed
    # outright - its process supervisor treats a worker gone quiet that long as hung, taking
    # down live traffic. Parsed off-thread instead; poll GET .../material-master, which reports
    # processing=true until it is done (see material_master_status).
    start_background_parse(group_id)
    return {"ok": True, "attached": True, "processing": True, "file_name": name, "materials": 0}


@router.get("/{group_id}/material-master")
def material_master_status(
    group_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> dict:
    """Is a master attached to this customer, and how many materials does it hold?

    Never parses live: a fresh upload (or a first read after the process restarted and the
    in-memory cache is gone) may have nothing cached yet, and parsing a real master inline here
    is exactly what was observed to get the worker answering this request killed. Reports
    processing=true instead and kicks off (or confirms) a background parse - poll again in a
    few seconds.
    """
    from app.core.material_master import (
        cached_mapping, is_building, master_path, original_name, sheet_headers,
        start_background_parse,
    )

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Template group not found")
    path = master_path(group_id)
    if not path.exists():
        return {"attached": False, "materials": 0}
    file_name = original_name(group_id) or path.name
    if is_building(group_id):
        return {"attached": True, "processing": True, "file_name": file_name, "materials": 0}
    mapping = cached_mapping(group_id)
    if mapping is None:
        # Nothing cached yet and no parse in flight - self-heals a master that was uploaded
        # before this endpoint learned to report processing, or one whose parse thread died
        # without leaving anything behind.
        start_background_parse(group_id)
        return {"attached": True, "processing": True, "file_name": file_name, "materials": 0}
    sample = list(mapping.items())[:5]
    return {"attached": True, "ok": bool(mapping), "file_name": file_name,
            "materials": len(mapping), "columns": sheet_headers(group_id),
            "sample": [{"material": k, "cth": v} for k, v in sample]}


@router.get("/{group_id}/material-master/columns")
def material_master_columns(
    group_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> dict:
    """The reference sheet's own column headings, so a field can be pointed at them by name."""
    from app.core.material_master import sheet_headers

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Template group not found")
    return {"columns": sheet_headers(group_id)}


@router.delete("/{group_id}/material-master")
def delete_material_master(
    group_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Remove the master. Lookup fields then fall back to reading the code off the documents."""
    from app.core.material_master import clear_cache, master_path

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Template group not found")
    clear_cache(group_id)
    removed = 0
    # The .* glob takes the .name sidecar too, which is what we want here.
    for stale in master_path(group_id).parent.glob(f"{group_id}.*"):
        try:
            stale.unlink()
            removed += 1
        except OSError:
            logger.warning("could not remove the material master %s", stale)
    return {"ok": True, "removed": removed}
