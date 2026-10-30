"""Give the insurance its own currency instead of always declaring it in rupees.

The ERP upload sheet has always carried an Ins_Currency column, but it was mapped as a bare
literal - {"column": "N", "header": "Ins_Currency", "field": "", "value": "INR"} - so EVERY
customs entry this system has produced declared the insurance in INR regardless of what the
invoice said, while the freight and misc-charge currencies beside it were read off the
document properly.

This does two things per affected template:

  * creates an "Ins_Currency" custom field the operator can answer - optional, NOT mandatory,
    so no in-flight job suddenly refuses to submit over a box that did not exist when it was
    created
  * rewires that column into a CHAIN (see _rows_for_plan): the operator's own answer, then
    Frt_Currency (the freight currency already read off this very document), and only then
    the "INR" that was being written anyway

Keeping "INR" as the last link is what makes this safe to deploy to live templates: a job
where nobody answers the new field and no freight currency was read exports exactly the byte
it exported before.

Deliberately narrow. Only a column whose header is Ins_Currency AND which has no field of its
own is touched - a template someone has already wired up by hand is left exactly as it is, and
a group that already has an Ins_Currency field does not get a second one. That also makes this
safe to re-run.

Revision ID: w3d8inscur1
Revises: v2c7manent1
Create Date: 2026-10-30
"""
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "w3d8inscur1"
down_revision = "v2c7manent1"
branch_labels = None
depends_on = None

LABEL = "Ins_Currency"
FALLBACK = "Frt_Currency"
HINT = (
    "Currency the insurance is in. Left blank, the freight currency on this document is used, "
    "and INR only if that is blank too."
)


def _targets(conn):
    """(group_id, tenant_id, config) for every template whose Ins_Currency column is still the
    bare hardcoded literal."""
    out = []
    for gid, tid, cfg in conn.execute(sa.text(
        "SELECT id, tenant_id, excel_config FROM template_groups WHERE excel_config IS NOT NULL"
    )):
        config = cfg if isinstance(cfg, dict) else json.loads(cfg or "{}")
        for sheet in config.get("sheets") or []:
            if not isinstance(sheet, dict):
                continue
            for col in sheet.get("headers") or sheet.get("columns") or []:
                if (
                    isinstance(col, dict)
                    and (col.get("header") or "").strip() == LABEL
                    and not (col.get("field") or "").strip()
                ):
                    out.append((gid, tid, config))
                    break
            else:
                continue
            break
    return out


def _save(conn, group_id, config):
    conn.execute(
        sa.text("UPDATE template_groups SET excel_config = CAST(:c AS JSON) WHERE id = :g"),
        {"c": json.dumps(config), "g": group_id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for group_id, tenant_id, config in _targets(conn):
        exists = conn.execute(
            sa.text("SELECT id FROM custom_fields WHERE group_id = :g AND label_name = :l"),
            {"g": group_id, "l": LABEL},
        ).first()
        if exists is None:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_fields (id, tenant_id, group_id, label_name, kind,"
                    " ask_operator, ask_operator_required, ask_operator_hint, per_row,"
                    " created_at, updated_at)"
                    " VALUES (:i, :t, :g, :l, 'hardcoded', true, false, :h, false, now(), now())"
                ),
                {"i": str(uuid.uuid4()), "t": tenant_id, "g": group_id, "l": LABEL, "h": HINT},
            )
        for sheet in config.get("sheets") or []:
            if not isinstance(sheet, dict):
                continue
            for col in sheet.get("headers") or sheet.get("columns") or []:
                if (
                    isinstance(col, dict)
                    and (col.get("header") or "").strip() == LABEL
                    and not (col.get("field") or "").strip()
                ):
                    col["field"] = LABEL
                    col["fallback_fields"] = [FALLBACK]
                    # "value" is left exactly as it was - it is now the LAST link of the
                    # chain rather than the only one, which is what keeps today's output
                    # intact for a job that answers neither field.
        _save(conn, group_id, config)


def downgrade() -> None:
    """Back to the bare literal. The custom field is dropped with its values - it fed nothing
    but this one column, so nothing else loses an answer."""
    conn = op.get_bind()
    for gid, _tid, cfg in conn.execute(sa.text(
        "SELECT id, tenant_id, excel_config FROM template_groups WHERE excel_config IS NOT NULL"
    )).fetchall():
        config = cfg if isinstance(cfg, dict) else json.loads(cfg or "{}")
        touched = False
        for sheet in config.get("sheets") or []:
            if not isinstance(sheet, dict):
                continue
            for col in sheet.get("headers") or sheet.get("columns") or []:
                if isinstance(col, dict) and (col.get("field") or "").strip() == LABEL:
                    col["field"] = ""
                    col.pop("fallback_fields", None)
                    touched = True
        if touched:
            _save(conn, gid, config)
    conn.execute(sa.text(
        "DELETE FROM job_field_values WHERE custom_field_id IN"
        " (SELECT id FROM custom_fields WHERE label_name = :l)"
    ), {"l": LABEL})
    conn.execute(sa.text("DELETE FROM custom_fields WHERE label_name = :l"), {"l": LABEL})
