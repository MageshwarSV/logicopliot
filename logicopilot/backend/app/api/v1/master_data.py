"""ERP Master Data (Tenant Admin -> Administration -> Master Data).

A Tenant Admin's own view of the reference tables ("dumps") that the job screen resolves ERP
values from: the importer tables (ERP Consignee Name, its branch, and the branch address) and
the quotation tables (ERP Quotation Name, its branch, and the quotation address). Each template
group keeps its own copy, so every table is listed per template.

No new storage: these are the same custom_field_reference_values rows that the job screen
reads, so a change saved here applies to the next lookup on a job. The add, edit and upload
paths reuse field_marks' staging helpers, so the conflict handling is identical (a key that is
already on file with a different value is reported, never silently overwritten).

Unlike field_marks' /reference-values endpoints, these do not require the field to be
kind="lookup" or is_target_value: ERP Branch Address and quotation_address are plain hardcoded
fields that still own a reference table. Access is limited to the fixed label list below, so
no other field's table can be reached through here.
"""
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.v1.field_marks import (
    ManualReferenceRowsPayload,
    _delete_reference_value_row,
    _rows_from_payload,
    _stage_reference_rows,
    _update_reference_value_row,
    _upload_reference_values,
)
from app.core.deps import get_db, require_role
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN, User

router = APIRouter(prefix="/master-data", tags=["master data"])

# category -> tables, in display order. "columns" names match_value_1..N; "value" names
# resolved_value. Labels are matched case-insensitively against CustomField.label_name.
CATEGORIES: dict[str, list[dict]] = {
    "importer": [
        {
            "key": "name",
            "label": "ERP Consignee Name",
            "title": "Importer Name",
            "description": "Importer name as read from the document, per country, and the ERP's own name for it.",
            "columns": ["Importer Name", "Country"],
            "value": "ERP Importer Name",
        },
        {
            "key": "branch",
            "label": "ERP Importer Address / Branch Code",
            "title": "Importer Branch",
            "description": "Branches on file for each ERP importer name.",
            "columns": ["ERP Importer Name", "Branch"],
            "value": "ERP Branch Code",
        },
        {
            "key": "address",
            "label": "ERP Branch Address",
            "title": "Branch Address",
            "description": "The address filled in for an importer name, branch and country.",
            "columns": ["ERP Importer Name", "Branch", "Country"],
            "value": "Address",
        },
    ],
    "quotation": [
        {
            "key": "name",
            "label": "Erp Entry Name",
            "title": "Quotation Name",
            "description": "Quotation name as read from the document, and the ERP quotation name it maps to.",
            "columns": ["Document Quotation Name"],
            "value": "ERP Quotation Name",
        },
        {
            "key": "branch",
            "label": "branch_name",
            "title": "Quotation Branch",
            "description": "Branches on file for each ERP quotation name.",
            "columns": ["ERP Quotation Name"],
            "value": "ERP Quotation Branch",
        },
        {
            "key": "address",
            "label": "quotation_address",
            "title": "Quotation Address",
            "description": "The address shown for an ERP quotation name and branch.",
            "columns": ["ERP Quotation Name", "ERP Quotation Branch"],
            "value": "Address",
        },
    ],
}

MATCH_COLUMNS = [
    CustomFieldReferenceValue.match_value_1,
    CustomFieldReferenceValue.match_value_2,
    CustomFieldReferenceValue.match_value_3,
    CustomFieldReferenceValue.match_value_4,
]


def _category(category: str) -> list[dict]:
    tables = CATEGORIES.get(category)
    if tables is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown master data category")
    return tables


def _table_for_field(db: Session, user: User, category: str, field_id: str) -> tuple[CustomField, dict]:
    """The custom field behind one master-data table, checked to be the caller's own tenant's
    and one of this category's fixed labels. Anything else is a 404, never a 403, so field ids
    from other tenants or other fields can't be probed."""
    tables = _category(category)
    cf = db.get(CustomField, field_id)
    if cf is None or cf.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    label = (cf.label_name or "").strip().lower()
    for table in tables:
        if table["label"].lower() == label:
            return cf, table
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")


def _row_out(r: CustomFieldReferenceValue) -> dict:
    return {
        "id": r.id,
        "match_values": [r.match_value_1 or "", r.match_value_2 or "", r.match_value_3 or "", r.match_value_4 or ""],
        "resolved_value": r.resolved_value,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/{category}")
def list_tables(
    category: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    """Every template of the caller's tenant that has at least one of this category's tables,
    with each table's field id and row count."""
    tables = _category(category)
    labels = {t["label"].lower(): t for t in tables}
    fields = (
        db.query(CustomField, TemplateGroup.name)
        .join(TemplateGroup, TemplateGroup.id == CustomField.group_id)
        .filter(CustomField.tenant_id == user.tenant_id)
        .filter(func.lower(CustomField.label_name).in_(list(labels)))
        .all()
    )
    counts = dict(
        db.query(CustomFieldReferenceValue.custom_field_id, func.count(CustomFieldReferenceValue.id))
        .filter(CustomFieldReferenceValue.custom_field_id.in_([cf.id for cf, _ in fields] or [""]))
        .group_by(CustomFieldReferenceValue.custom_field_id)
        .all()
    )
    templates: dict[str, dict] = {}
    for cf, group_name in fields:
        t = templates.setdefault(cf.group_id, {"group_id": cf.group_id, "name": group_name, "tables": {}})
        table = labels[cf.label_name.strip().lower()]
        t["tables"][table["key"]] = {"field_id": cf.id, "rows": counts.get(cf.id, 0)}
    return {
        "tables": [{k: t[k] for k in ("key", "title", "description", "columns", "value")} for t in tables],
        "templates": sorted(templates.values(), key=lambda t: t["name"].lower()),
    }


@router.get("/{category}/fields/{field_id}/rows")
def list_rows(
    category: str,
    field_id: str,
    q: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    """One page of a table, optionally filtered by a case-insensitive search across every key
    column and the value. Paged on the server: some of these tables run to 70,000+ rows."""
    cf, _ = _table_for_field(db, user, category, field_id)
    query = db.query(CustomFieldReferenceValue).filter(CustomFieldReferenceValue.custom_field_id == cf.id)
    term = q.strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(or_(
            *[col.ilike(like, escape="\\") for col in MATCH_COLUMNS],
            CustomFieldReferenceValue.resolved_value.ilike(like, escape="\\"),
        ))
    total = query.count()
    rows = (
        query.order_by(CustomFieldReferenceValue.match_value_1, CustomFieldReferenceValue.match_value_2,
                       CustomFieldReferenceValue.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "rows": [_row_out(r) for r in rows]}


@router.post("/{category}/fields/{field_id}/rows")
def add_rows(
    category: str,
    field_id: str,
    payload: ManualReferenceRowsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    cf, _ = _table_for_field(db, user, category, field_id)
    return _stage_reference_rows(db, _rows_from_payload(payload), custom_field_id=cf.id)


class RowValueUpdate(BaseModel):
    resolved_value: str


@router.patch("/{category}/fields/{field_id}/rows/{row_id}")
def update_row(
    category: str,
    field_id: str,
    row_id: int,
    payload: RowValueUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    cf, _ = _table_for_field(db, user, category, field_id)
    _update_reference_value_row(db, row_id, {"resolved_value": payload.resolved_value}, custom_field_id=cf.id)
    row = db.get(CustomFieldReferenceValue, row_id)
    return _row_out(row)


@router.delete("/{category}/fields/{field_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(
    category: str,
    field_id: str,
    row_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> None:
    cf, _ = _table_for_field(db, user, category, field_id)
    _delete_reference_value_row(db, row_id, custom_field_id=cf.id)


@router.post("/{category}/fields/{field_id}/upload")
def upload_rows(
    category: str,
    field_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    """Add rows from a filled-in template. Columns are read by position: the table's key
    columns in slots 1-4 (unused slots blank), the value in column 5."""
    cf, _ = _table_for_field(db, user, category, field_id)
    return _upload_reference_values(db, file, custom_field_id=cf.id)


def _headers(table: dict) -> list[str]:
    keys = list(table["columns"]) + [f"(leave blank {i})" for i in range(len(table["columns"]) + 1, 5)]
    return keys + [table["value"]]


def _workbook_response(table: dict, rows, filename: str) -> Response:
    import openpyxl

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(table["title"][:31])
    ws.append(_headers(table))
    for r in rows:
        ws.append([r.match_value_1 or "", r.match_value_2 or "", r.match_value_3 or "",
                   r.match_value_4 or "", r.resolved_value or ""])
    buffer = io.BytesIO()
    wb.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _file_stem(group_name: str, table: dict) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in f"{group_name}_{table['title']}")
    return "_".join(p for p in safe.split("_") if p)


@router.get("/{category}/fields/{field_id}/template")
def download_template(
    category: str,
    field_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> Response:
    cf, table = _table_for_field(db, user, category, field_id)
    group = db.get(TemplateGroup, cf.group_id)
    return _workbook_response(table, [], f"{_file_stem(group.name if group else '', table)}_template.xlsx")


@router.get("/{category}/fields/{field_id}/export")
def export_rows(
    category: str,
    field_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> Response:
    """The whole table as a workbook in the upload template's own column layout."""
    cf, table = _table_for_field(db, user, category, field_id)
    group = db.get(TemplateGroup, cf.group_id)
    rows = (
        db.query(CustomFieldReferenceValue)
        .filter(CustomFieldReferenceValue.custom_field_id == cf.id)
        .order_by(CustomFieldReferenceValue.match_value_1, CustomFieldReferenceValue.match_value_2,
                  CustomFieldReferenceValue.id)
        .yield_per(2000)
    )
    return _workbook_response(table, rows, f"{_file_stem(group.name if group else '', table)}.xlsx")
