"""The template frozen per job, so workers never read template config from the database.

When a job starts on the pipeline, its template - the template group, every template document
in order, each document's field marks, and the group's custom fields - is copied into
work/template.json, once. Workers read that copy. An admin editing the template while the job
runs therefore cannot leave half its documents read with the old prompts and half with the new,
and the job records exactly which template version it was read with ("version", a short hash of
the content), so a result can always be explained later.

The copy is generic - every mapped column of each row - so a column added to these models later
is carried along without this file changing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.pipeline import workdir


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _columns(obj) -> dict:
    return {attr.key: _plain(getattr(obj, attr.key)) for attr in inspect(obj).mapper.column_attrs}


def build(db: Session, group_id: str) -> dict:
    """The template as plain data, with a content version. Raises LookupError for a missing group."""
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise LookupError(f"template group {group_id} not found")
    documents = []
    for tdoc in (db.query(TemplateDocument).filter(TemplateDocument.group_id == group_id)
                 .order_by(TemplateDocument.order_index, TemplateDocument.id).all()):
        marks = (db.query(FieldMark).filter(FieldMark.document_id == tdoc.id)
                 .order_by(FieldMark.id).all())
        documents.append({**_columns(tdoc), "marks": [_columns(m) for m in marks]})
    custom_fields = [_columns(c) for c in db.query(CustomField).filter(CustomField.group_id == group_id)
                     .order_by(CustomField.id).all()]
    content = {"group": _columns(group), "documents": documents, "custom_fields": custom_fields}
    raw = json.dumps(content, sort_keys=True, default=str, ensure_ascii=False)
    version = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return {"version": version, **content}


def freeze(db: Session, job_id: str, group_id: str) -> dict:
    """Write the job's template.json the first time; afterwards always return that same copy -
    a resumed job keeps the template it started with."""
    existing = workdir.read_json(workdir.template_path(job_id))
    if existing is not None:
        return existing
    snap = build(db, group_id)
    workdir.write_json(workdir.template_path(job_id), snap)
    return workdir.read_json(workdir.template_path(job_id)) or snap


def load(job_id: str) -> dict | None:
    return workdir.read_json(workdir.template_path(job_id))
