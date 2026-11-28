"""One-off: move one finished template - with its reference data and its ERP script - from one
server to another (SIT -> production).

What travels, all with their ORIGINAL ids (so the screen layouts, cross-checks and the ERP
script's template link keep pointing at the right fields):
  template_groups (the template), template_documents (+ their sample PDFs), field_marks,
  custom_fields, custom_field_reference_values (the dumps: exporter/importer branches,
  quotations, ports ...), cross_doc_links, screen_layouts, document_samples, erp_workbooks
  (+ file), erp_fields, the Excel import template file, and the named ERP script.
What does NOT travel: jobs, users, user assignments, mailboxes, correction history - those
belong to the server they were made on.

  export (on the source server, backend folder):
    PYTHONPATH=. venv/bin/python ../deploy/oneoff/template_bundle.py export \
        --tenant "4S Logistics" --group "Sea Export(November 25)" \
        --script "SOFTLINK Sea Export (November 25)" --out /tmp/sea_export_bundle.tar.gz

  import (on the target's app server, backend folder):
    PYTHONPATH=. venv/bin/python ../deploy/oneoff/template_bundle.py import /tmp/sea_export_bundle.tar.gz
        -> DRY RUN: checks everything, inserts inside a transaction, rolls it back, writes no file
    ... add --apply to really write it.
    ... add --branch-login to keep the script's branch-login routing (branch_field). Without
        it the script logs in with its own recorded user, from step 1.

Safe by default: STOPS if the template (by id or by name) or any of its rows already exists
on the target - it never overwrites a template. The ERP script is the one exception: an
existing script with the same name is UPDATED (its old steps are written to a JSON backup
first), because production already has an earlier, unfinished copy of it.
"""
import argparse
import base64
import datetime as dt
import decimal
import io
import json
import sys
import tarfile
import uuid
from pathlib import Path

import app.main  # noqa: F401 - loads every model
from sqlalchemy import Date, DateTime, Integer, select
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal

T = Base.metadata.tables
# insert order (parents first)
ORDER = ["template_groups", "template_documents", "field_marks", "custom_fields",
         "custom_field_reference_values", "cross_doc_links", "screen_layouts", "document_samples",
         "erp_workbooks", "erp_fields"]
PATH_COLS = {("template_documents", "file_path"), ("erp_workbooks", "stored_path")}


def enc(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"__b64": base64.b64encode(bytes(v)).decode()}
    return v


def dec(table, col, v):
    if v is None:
        return None
    if isinstance(v, dict) and set(v) == {"__b64"}:
        return base64.b64decode(v["__b64"])
    t = T[table].c[col].type
    if isinstance(t, DateTime) and isinstance(v, str):
        return dt.datetime.fromisoformat(v)
    if isinstance(t, Date) and isinstance(v, str):
        return dt.date.fromisoformat(v)
    return v


def rows(db, table, col, ids):
    if not ids:
        return []
    t = T[table]
    return [{k: enc(v) for k, v in r.items()} for r in db.execute(select(t).where(t.c[col].in_(ids))).mappings()]


# ------------------------------------------------------------------------------------- export
def export(a):
    from app.models.tenant import Tenant
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.name == a.tenant).first() or sys.exit(f"STOP: no tenant {a.tenant!r}")
    tg = T["template_groups"]
    g = db.execute(select(tg).where(tg.c.tenant_id == tenant.id, tg.c.name == a.group)).mappings().first()
    if g is None:
        sys.exit(f"STOP: no template {a.group!r} for {a.tenant}")
    G = g["id"]
    out = {"tenant_name": tenant.name, "group_id": G, "group_name": a.group,
           "uploads_dir": str(get_settings().uploads_dir), "exported_at": dt.datetime.now().isoformat(),
           "tables": {}}
    tb = out["tables"]
    tb["template_groups"] = rows(db, "template_groups", "id", [G])
    tb["template_documents"] = rows(db, "template_documents", "group_id", [G])
    docs = [r["id"] for r in tb["template_documents"]]
    tb["field_marks"] = rows(db, "field_marks", "document_id", docs)
    tb["custom_fields"] = rows(db, "custom_fields", "group_id", [G])
    marks = [r["id"] for r in tb["field_marks"]]
    cfs = [r["id"] for r in tb["custom_fields"]]
    rv = rows(db, "custom_field_reference_values", "custom_field_id", cfs)
    rv += [r for r in rows(db, "custom_field_reference_values", "mark_id", marks) if r["custom_field_id"] is None]
    tb["custom_field_reference_values"] = rv
    tb["cross_doc_links"] = rows(db, "cross_doc_links", "group_id", [G])
    tb["screen_layouts"] = rows(db, "screen_layouts", "group_id", [G])
    tb["document_samples"] = rows(db, "document_samples", "template_document_id", docs)
    tb["erp_workbooks"] = rows(db, "erp_workbooks", "group_id", [G])
    tb["erp_fields"] = rows(db, "erp_fields", "group_id", [G])
    # a recording sample job is a job of THIS server - meaningless anywhere else
    for r in tb["template_groups"]:
        if isinstance(r.get("excel_config"), dict):
            r["excel_config"].pop("sample_job_id", None)
    es = T["erp_scripts"]
    out["erp_script"] = None
    if a.script:
        s = db.execute(select(es).where(es.c.tenant_id == tenant.id, es.c.name == a.script)).mappings().first()
        if s is None:
            sys.exit(f"STOP: no ERP script {a.script!r}")
        out["erp_script"] = {k: enc(v) for k, v in s.items()}

    up = Path(get_settings().uploads_dir)
    dirs = [up / "documents" / d for d in docs] + [up / "excel_templates" / G]
    dirs += [up / "onboarding_erp" / r["id"] for r in tb["erp_workbooks"]]
    for r in tb["erp_workbooks"]:
        if r.get("stored_path"):
            dirs.append(Path(r["stored_path"]).parent)
    files = sorted({p for d in dirs if d.is_dir() for p in d.rglob("*") if p.is_file()})
    with tarfile.open(a.out, "w:gz") as tar:
        data = json.dumps(out).encode()
        info = tarfile.TarInfo("bundle.json"); info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for p in files:
            tar.add(str(p), arcname="files/" + str(p.relative_to(up)) if p.is_relative_to(up) else "files/" + str(p))
    print(f"template {a.group!r} ({G})")
    for k in ORDER:
        print(f"  {k:32} {len(tb[k]):>7} rows")
    print(f"  ERP script: {a.script!r}" if a.script else "  no ERP script")
    print(f"  files: {len(files)}")
    for p in files:
        print("    ", p)
    print("written:", a.out)


# ------------------------------------------------------------------------------------- import
def do_import(a):
    from app.models.tenant import Tenant
    with tarfile.open(a.bundle, "r:gz") as tar:
        b = json.loads(tar.extractfile("bundle.json").read())
        files = {m.name[len("files/"):]: tar.extractfile(m).read() for m in tar.getmembers()
                 if m.isfile() and m.name.startswith("files/")}
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.name == b["tenant_name"]).first()
    if tenant is None:
        sys.exit(f"STOP: no tenant named {b['tenant_name']!r} here")
    G = b["group_id"]; tb = b["tables"]
    tg = T["template_groups"]
    clash = db.execute(select(tg.c.id, tg.c.name).where(
        (tg.c.id == G) | ((tg.c.tenant_id == tenant.id) & (tg.c.name == b["group_name"])))).all()
    if clash:
        sys.exit(f"STOP: this server already has the template: {clash} - nothing done")
    for k in ORDER:
        pk = [c for c in T[k].primary_key.columns][0]
        ids = [r[pk.name] for r in tb[k] if not isinstance(pk.type, Integer)]
        for i in range(0, len(ids), 1000):
            n = db.execute(select(pk).where(pk.in_(ids[i:i + 1000]))).first()
            if n:
                sys.exit(f"STOP: {k} row {n[0]} already exists here - nothing done")

    src_up = b["uploads_dir"].rstrip("/"); dst_up = str(get_settings().uploads_dir).rstrip("/")
    bundled = set(ORDER)
    exists_cache: dict = {}

    def exists(table, col, v):
        key = (table, col, v)
        if key not in exists_cache:
            t = T[table]
            exists_cache[key] = db.execute(select(t.c[col]).where(t.c[col] == v)).first() is not None
        return exists_cache[key]

    cleared = {}
    print(f"importing {b['group_name']!r} for {tenant.name} ({tenant.id})"
          f"{'' if a.apply else '  - DRY RUN'}")
    for k in ORDER:
        t = T[k]; out = []
        int_pk = [c.name for c in t.primary_key.columns if isinstance(c.type, Integer)]
        for r in tb[k]:
            row = {}
            for col, v in r.items():
                if col not in t.c or col in int_pk:
                    continue
                v = dec(k, col, v)
                if col == "tenant_id":
                    v = tenant.id
                if (k, col) in PATH_COLS and isinstance(v, str) and v.startswith(src_up + "/") and src_up != dst_up:
                    v = dst_up + v[len(src_up):]
                for fk in t.c[col].foreign_keys:
                    ref = fk.column.table.name
                    if v is not None and ref not in bundled and ref != "tenants" and not exists(ref, fk.column.name, v):
                        if not t.c[col].nullable:
                            sys.exit(f"STOP: {k}.{col} needs {ref} {v!r}, which this server does not have")
                        cleared[(k, col)] = cleared.get((k, col), 0) + 1
                        v = None
                row[col] = v
            out.append(row)
        for i in range(0, len(out), 2000):
            if out[i:i + 2000]:
                db.execute(t.insert(), out[i:i + 2000])
        print(f"  {k:32} {len(out):>7} rows")
    for (k, col), n in cleared.items():
        print(f"  note: {k}.{col} pointed at something only the source server has - left empty on {n} row(s)")

    s = b.get("erp_script")
    if s:
        es = T["erp_scripts"]
        cur = db.execute(select(es).where(es.c.tenant_id == tenant.id, es.c.name == s["name"])).mappings().first()
        upd = {k: dec("erp_scripts", k, s[k]) for k in ("url", "has_login", "login_username", "login_password",
                                                         "steps", "checkpoint_index", "stay_open", "status", "notes")
               if k in s and k in es.c}
        upd["template_ids"] = [G]
        upd["branch_field"] = s.get("branch_field") if a.branch_login else None
        if cur is None:
            row = {k: dec("erp_scripts", k, v) for k, v in s.items() if k in es.c}
            row.update(upd); row["tenant_id"] = tenant.id
            if exists("erp_scripts", "id", row["id"]):
                row.pop("id")
            db.execute(es.insert(), [row])
            print(f"  ERP script {s['name']!r}: created ({len(s['steps'] or [])} steps)")
        else:
            if a.apply:
                Path(f"/tmp/erp_script_backup_{cur['id']}.json").write_text(
                    json.dumps({k: enc(v) for k, v in cur.items()}, default=str))
            db.execute(es.update().where(es.c.id == cur["id"]).values(**upd))
            print(f"  ERP script {s['name']!r}: updated ({len(cur['steps'] or [])} -> {len(s['steps'] or [])} steps)"
                  + (f", old copy saved to /tmp/erp_script_backup_{cur['id']}.json" if a.apply else ""))
        print(f"    branch login routing: {upd['branch_field'] or 'off - logs in with its own user from step 1'}")

    print(f"  files: {len(files)} -> {dst_up}/")
    if not a.apply:
        db.rollback()
        print("DRY RUN - nothing written. Run again with --apply.")
        return
    for rel, data in files.items():
        p = Path(dst_up) / rel if not rel.startswith("/") else Path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            print("    kept existing file", p)
            continue
        p.write_bytes(data)
    db.commit()
    print("DONE - written.")


ap = argparse.ArgumentParser()
sp = ap.add_subparsers(dest="cmd", required=True)
e = sp.add_parser("export")
e.add_argument("--tenant", required=True); e.add_argument("--group", required=True)
e.add_argument("--script"); e.add_argument("--out", required=True)
i = sp.add_parser("import")
i.add_argument("bundle"); i.add_argument("--apply", action="store_true"); i.add_argument("--branch-login", action="store_true")
args = ap.parse_args()
export(args) if args.cmd == "export" else do_import(args)
