"""Give already-extracted jobs a box for every Container Detail column on every container row.

run_extraction now does this itself at the end of every extraction
(_backfill_missing_standalone_cells in app/api/v1/jobs.py). Jobs extracted before that change
can still show a "Container 1" card with a container type and no container-number box
(JOB-88572C) - nothing to type the number into, and the ERP import goes out with it blank.
This creates those missing cells, empty, on every job that is not yet completed.

Same rule as the extraction-time helper: marks that are standalone tables on the same template
document and share a heading are columns of one table; a column only gets a cell on a row some
other column of that table already has. Rows are never added. Completed jobs are left alone.

Data only - no schema change. Downgrade removes nothing: the cells it created are
indistinguishable from ones an operator has since typed into.

Revision ID: h5ctrbox01
Revises: g4erpsby01
Create Date: 2026-11-13
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "h5ctrbox01"
down_revision = "g4erpsby01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    marks = conn.execute(sa.text(
        "SELECT id, document_id, label_name, standalone_group_heading FROM field_marks "
        "WHERE is_multi_value = :t AND standalone_multi_value = :t"
    ), {"t": True}).all()

    tables: dict[tuple[str, str], list] = {}
    for m in marks:
        key = (m.document_id, (m.standalone_group_heading or "").strip().lower())
        tables.setdefault(key, []).append(m)

    now = datetime.now(timezone.utc)
    insert = sa.text(
        "INSERT INTO job_field_values (id, tenant_id, job_id, mark_id, template_document_id, "
        "job_document_id, set_index, label_name, row_index, extracted_value, created_at, updated_at) "
        "VALUES (:id, :tenant_id, :job_id, :mark_id, :template_document_id, :job_document_id, "
        ":set_index, :label_name, :row_index, NULL, :now, :now)"
    )
    for (document_id, _heading), cols in tables.items():
        if len(cols) < 2:
            continue
        ids = [c.id for c in cols]
        cells = conn.execute(
            sa.text(
                "SELECT v.tenant_id, v.job_id, v.job_document_id, v.row_index, v.mark_id, v.set_index "
                "FROM job_field_values v JOIN jobs j ON j.id = v.job_id "
                "WHERE v.mark_id IN :ids AND v.row_index IS NOT NULL "
                "AND v.job_document_id IS NOT NULL AND j.status <> 'completed'"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": ids},
        ).all()
        rows: dict[tuple[str, int], dict] = {}
        for c in cells:
            r = rows.setdefault((c.job_document_id, c.row_index),
                                {"have": set(), "tenant_id": c.tenant_id, "job_id": c.job_id,
                                 "set_index": c.set_index})
            r["have"].add(c.mark_id)
        for (job_document_id, row_index), r in rows.items():
            for col in cols:
                if col.id in r["have"]:
                    continue
                conn.execute(insert, {
                    "id": str(uuid.uuid4()), "tenant_id": r["tenant_id"], "job_id": r["job_id"],
                    "mark_id": col.id, "template_document_id": document_id,
                    "job_document_id": job_document_id, "set_index": r["set_index"],
                    "label_name": col.label_name, "row_index": row_index, "now": now,
                })


def downgrade() -> None:
    pass
