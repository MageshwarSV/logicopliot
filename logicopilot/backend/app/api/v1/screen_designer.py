"""Super Admin -> Screen Designer: a template document's job-screen layout.

Placement only (sections, their order, the product card) - see app.models.screen_layout and
app.core.screen_layout. Saves straight away, and every save is kept in screen_layout_history.
A document with no layout keeps the job screen's existing behaviour."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role, require_write_access
from app.core.screen_layout import LayoutError, allowed_refs, build_from_current, clean_layout
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job
from app.models.screen_layout import ScreenLayout, ScreenLayoutHistory
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/screen-designer", tags=["screen designer"])


class LayoutIn(BaseModel):
    layout: dict


def _document(db: Session, document_id: str) -> TemplateDocument:
    doc = db.get(TemplateDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _fields(db: Session, doc: TemplateDocument) -> tuple[list, list]:
    marks = db.query(FieldMark).filter(FieldMark.document_id == doc.id).all()
    cfs = db.query(CustomField).filter(CustomField.group_id == doc.group_id).all()
    return marks, cfs


def _out(row: ScreenLayout | None, dropped: list[str] | None = None) -> dict:
    if row is None:
        return {"layout": None, "updated_at": None, "updated_by": None}
    out = {"layout": row.layout, "updated_at": row.updated_at.isoformat() if row.updated_at else None,
           "updated_by": row.updated_by_name}
    if dropped:
        out["dropped"] = dropped
    return out


def _save(db: Session, doc: TemplateDocument, layout: dict, user: User, action: str) -> ScreenLayout:
    row = db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first()
    name = user.full_name or user.email
    if row is None:
        row = ScreenLayout(tenant_id=doc.tenant_id, group_id=doc.group_id,
                           template_document_id=doc.id, layout=layout)
        db.add(row)
    row.layout = layout
    row.updated_by_id, row.updated_by_name = user.id, name
    db.add(ScreenLayoutHistory(template_document_id=doc.id, layout=layout, action=action,
                               saved_by_id=user.id, saved_by_name=name))
    db.commit()
    db.refresh(row)
    return row


@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """Every template with its documents, and which documents have a layout."""
    with_layout = {d for (d,) in db.query(ScreenLayout.template_document_id).all()}
    out = []
    for g in db.query(TemplateGroup).order_by(TemplateGroup.name).all():
        docs = (db.query(TemplateDocument).filter(TemplateDocument.group_id == g.id)
                .order_by(TemplateDocument.created_at).all())
        out.append({"id": g.id, "name": g.name, "mode": g.mode, "tenant_id": g.tenant_id,
                    "documents": [{"id": d.id, "name": d.name, "doc_type": d.doc_type,
                                   "has_layout": d.id in with_layout} for d in docs]})
    return out


@router.get("/documents/{document_id}/layout")
def get_layout(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    doc = _document(db, document_id)
    return _out(db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first())


@router.put("/documents/{document_id}/layout")
def save_layout(
    document_id: str,
    body: LayoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    doc = _document(db, document_id)
    marks, cfs = _fields(db, doc)
    section_refs, product_refs = allowed_refs(marks, cfs)
    try:
        layout, dropped = clean_layout(body.layout, section_refs, product_refs)
    except LayoutError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(_save(db, doc, layout, user, "save"), dropped)


@router.post("/documents/{document_id}/layout/from-current")
def create_from_current(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Saves the layout this document's job screen shows today - the starting point."""
    doc = _document(db, document_id)
    marks, cfs = _fields(db, doc)
    layout = build_from_current(marks, cfs, doc.id)
    section_refs, product_refs = allowed_refs(marks, cfs)
    layout, _dropped = clean_layout(layout, section_refs, product_refs)
    return _out(_save(db, doc, layout, user, "create_from_current"))


@router.delete("/documents/{document_id}/layout")
def remove_layout(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Back to the default screen. Kept in the history, so it can be restored."""
    doc = _document(db, document_id)
    row = db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first()
    if row is not None:
        db.delete(row)
        db.add(ScreenLayoutHistory(template_document_id=doc.id, layout=None, action="remove",
                                   saved_by_id=user.id, saved_by_name=user.full_name or user.email))
        db.commit()
    return _out(None)


@router.get("/groups/{group_id}/recent-jobs")
def recent_jobs(
    group_id: str,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """The template's latest jobs, for the designer's preview picker."""
    rows = (db.query(Job.id, Job.reference, Job.status, Job.created_at)
            .filter(Job.group_id == group_id).order_by(Job.created_at.desc()).limit(limit).all())
    return [{"id": r.id, "reference": r.reference, "status": r.status,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


def layouts_for_group(db: Session, group_id: str) -> dict[str, dict]:
    """{template_document_id: layout} for a template's documents that have one - sent with the
    job detail so the job screen can use it."""
    return {r.template_document_id: r.layout
            for r in db.query(ScreenLayout).filter(ScreenLayout.group_id == group_id).all()}
