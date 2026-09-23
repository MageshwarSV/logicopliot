from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_current_user, get_db, require_role
from app.models.erp_access import ErpAccessRequest
from app.models.field_mark import FieldMark
from app.models.template_group import TemplateGroup
from app.models.template_review import TemplateReview
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(tags=["reviews / inbox"])


# ---- schemas ----
class RequestChanges(BaseModel):
    message: str


class FormatPrompt(BaseModel):
    format_prompt: str


class FormatCheck(BaseModel):
    value: str | None = None
    format_prompt: str


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    group_id: str
    message: str
    status: str


class InboxItem(BaseModel):
    id: str
    kind: str  # "change_request" | "erp_access" | "job_failure"
    group_id: str | None = None
    job_id: str | None = None
    group_name: str | None = None
    tenant_name: str
    raised_by: str | None
    message: str
    status: str
    created_at: str
    # ERP-access requests only:
    erp_url: str | None = None
    erp_username: str | None = None
    erp_password: str | None = None


class ErpAccessCreate(BaseModel):
    url: str
    username: str
    password: str


def _tenant_group(db: Session, group_id: str, user: User) -> TemplateGroup:
    group = db.get(TemplateGroup, group_id)
    if group is None or (user.role == TENANT_ADMIN and group.tenant_id != user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return group


# ---- Super Admin: approve / reject a template ----
#
# Approving a template means signing off the fields, the marks and the transformations that
# were built for it - the work of whoever BUILT it, not of the customer it was built for. It
# used to sit in the Tenant Admin's workspace, so a customer was being asked to certify a
# configuration they had no way to judge, and it also gated the operators: nothing runs until
# a template reads `approved`. Moved to the Super Admin, screen and permission together - a
# menu item that merely disappears while the endpoint still answers is not moved, it is hidden.
@router.post("/template-groups/{group_id}/approve")
def approve_template(
    group_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
):
    group = _tenant_group(db, group_id, user)
    group.status = "approved"
    db.commit()
    return {"status": "approved"}


@router.post("/template-groups/{group_id}/request-changes", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def request_changes(
    group_id: str,
    payload: RequestChanges,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
) -> ReviewOut:
    group = _tenant_group(db, group_id, user)
    group.status = "changes_requested"
    review = TemplateReview(
        tenant_id=group.tenant_id,
        group_id=group.id,
        raised_by_id=user.id,
        message=payload.message,
        status="open",
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return ReviewOut.model_validate(review)


# ---- Super Admin: inbox ----
@router.get("/template-reviews", response_model=list[InboxItem])
def list_reviews(
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[InboxItem]:
    from app.models.tenant import Tenant

    reviews = (
        db.query(TemplateReview)
        .filter(TemplateReview.status == "open")
        .order_by(TemplateReview.created_at.desc())
        .all()
    )
    out: list[InboxItem] = []
    for r in reviews:
        group = db.get(TemplateGroup, r.group_id)
        tenant = db.get(Tenant, r.tenant_id)
        raiser = db.get(User, r.raised_by_id) if r.raised_by_id else None
        out.append(
            InboxItem(
                id=r.id,
                kind="change_request",
                group_id=r.group_id,
                group_name=group.name if group else "?",
                tenant_name=tenant.name if tenant else "?",
                raised_by=raiser.full_name if raiser else None,
                message=r.message,
                status=r.status,
                created_at=r.created_at.isoformat(),
            )
        )

    # ERP-access requests share the same inbox.
    erp = (
        db.query(ErpAccessRequest)
        .filter(ErpAccessRequest.status == "open")
        .order_by(ErpAccessRequest.created_at.desc())
        .all()
    )
    for e in erp:
        tenant = db.get(Tenant, e.tenant_id)
        raiser = db.get(User, e.raised_by_id) if e.raised_by_id else None
        out.append(
            InboxItem(
                id=e.id,
                kind="erp_access",
                tenant_name=tenant.name if tenant else "?",
                raised_by=raiser.full_name if raiser else None,
                message=f"ERP access requested for {e.url}",
                status=e.status,
                created_at=e.created_at.isoformat(),
                erp_url=e.url,
                erp_username=e.username,
                erp_password=e.password,
            )
        )

    # Failed web-entry (ERP) jobs also surface here.
    from app.models.job_failure import JobFailureReport

    failures = (
        db.query(JobFailureReport)
        .filter(JobFailureReport.status == "open")
        .order_by(JobFailureReport.created_at.desc())
        .all()
    )
    for f in failures:
        tenant = db.get(Tenant, f.tenant_id)
        oper = db.get(User, f.operator_id) if f.operator_id else None
        msg = (
            f"Web-entry FAILED — company: {tenant.name if tenant else '?'}, "
            f"operator: {oper.full_name if oper else '—'}, job: {f.job_reference or '—'}. "
            f"{f.reason or ''}"
        )
        out.append(
            InboxItem(
                id=f.id,
                kind="job_failure",
                job_id=f.job_id,
                tenant_name=tenant.name if tenant else "?",
                raised_by=oper.full_name if oper else None,
                message=msg,
                status=f.status,
                created_at=f.created_at.isoformat(),
                erp_url=f.erp_url,
            )
        )

    out.sort(key=lambda i: i.created_at, reverse=True)
    return out


@router.post("/job-failures/{failure_id}/resolve")
def resolve_job_failure(
    failure_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """Mark a failed-web-entry report resolved (removes it from the inbox)."""
    from app.models.job_failure import JobFailureReport

    rep = db.get(JobFailureReport, failure_id)
    if rep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    rep.status = "resolved"
    db.commit()
    return {"status": "resolved"}


@router.post("/erp-access-requests", status_code=status.HTTP_201_CREATED)
def create_erp_access(
    payload: ErpAccessCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN, SUPER_ADMIN)),
):
    """Tenant Admin submits ERP URL + credentials; lands in the Super Admin inbox."""
    req = ErpAccessRequest(
        tenant_id=user.tenant_id,
        raised_by_id=user.id,
        url=payload.url,
        username=payload.username,
        password=payload.password,
        status="open",
    )
    db.add(req)
    db.commit()
    return {"status": "submitted"}


@router.post("/erp-access-requests/{request_id}/resolve")
def resolve_erp_access(
    request_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    req = db.get(ErpAccessRequest, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    req.status = "resolved"
    db.commit()
    return {"status": "resolved"}


@router.post("/template-reviews/{review_id}/resolve")
def resolve_review(
    review_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    review = db.get(TemplateReview, review_id)
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    review.status = "resolved"
    db.commit()
    return {"status": "resolved"}


# ---- Super Admin: per-field format prompt (the data transformation itself) ----
# This is what the Data Transformation screen writes, so it moves with that screen. Leaving it
# open to the Tenant Admin would mean the menu item had gone while the ability had not.
@router.patch("/marks/{mark_id}/format")
def set_format_prompt(
    mark_id: str,
    payload: FormatPrompt,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
):
    mark = db.get(FieldMark, mark_id)
    if mark is None or (user.role == TENANT_ADMIN and mark.tenant_id != user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")
    mark.tenant_format_prompt = payload.format_prompt
    db.commit()
    return {"tenant_format_prompt": mark.tenant_format_prompt}


@router.post("/marks/{mark_id}/format-check")
def format_check(
    mark_id: str,
    payload: FormatCheck,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN)),
):
    """Preview the effect of a format prompt on a single field's value (that field only)."""
    mark = db.get(FieldMark, mark_id)
    if mark is None or (user.role == TENANT_ADMIN and mark.tenant_id != user.tenant_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")
    source_value = payload.value if payload.value is not None else (mark.example_value or "")
    settings = get_settings()
    if not settings.openai_api_key or not source_value:
        return {"formatted_value": source_value}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=20)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=100,
            messages=[
                {"role": "system", "content": "Reformat the given value per the instruction. Return only the reformatted value."},
                {"role": "user", "content": f"Value: {source_value}\nInstruction: {payload.format_prompt}"},
            ],
        )
        return {"formatted_value": (resp.choices[0].message.content or source_value).strip()}
    except Exception:  # noqa: BLE001
        return {"formatted_value": source_value}
