"""CTH Master: the customer's CTH dump workbook works as the database - list it, edit one row,
add one row or a small batch from the 3-column template. Never a full replace from here."""
import io
import time
import uuid

import openpyxl
import pytest

from app.core import material_master as mm
from app.core.config import get_settings
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

BASE = "/api/v1/master-data/cth-directory"
HEADERS = ("Material", "Material Description", "Comm./imp. code no.", "HS CODE MISSING/UPDATED IN ODOO")


@pytest.fixture(autouse=True)
def _uploads_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    mm.clear_cache()


def _xlsx(rows, headers) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append(list(headers))
    for r in rows:
        wb.active.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _setup(client, db_session, dump=True):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    admin = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH", kind="lookup"))
    db_session.commit()
    if dump:   # as the backend / Super Admin upload leaves it
        path = mm.master_path(group.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_xlsx([
            ("839599A", "COVER FRONT LH", "8517 79 90", "UPDATED"),
            (2827.01, "PLASTIC CLIP", 39269099, None),
            (None, "AL FOIL 5U", "76071910", None),
        ], HEADERS))
        mm.remember_name(group.id, "JUNE DUMP FILE.xlsx")
    assert login(client, admin.email).status_code == 200
    return group


def _ready(client, gid, **params):
    for _ in range(200):
        body = client.get(f"{BASE}/{gid}/rows", params=params).json()
        if body["ready"]:
            return body
        time.sleep(0.05)
    raise AssertionError("rows never became ready")


def _lookup(gid):
    for _ in range(200):
        if not mm.is_building(gid):
            break
        time.sleep(0.05)
    return mm.load_master(gid)


def _sheet(gid):
    ws = openpyxl.load_workbook(mm.master_path(gid), read_only=True).active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_list_search_and_sort(client, db_session):
    group = _setup(client, db_session)
    [tpl] = client.get(BASE).json()["templates"]
    assert tpl["file_name"] == "JUNE DUMP FILE.xlsx"
    body = _ready(client, group.id)
    assert body["total"] == 3
    assert [(r["part_code"], r["cth"]) for r in body["rows"]] == [("2827.01", "39269099"), ("839599A", "85177990"), ("", "76071910")]
    assert body["rows"][1]["cth_as_written"] == "8517 79 90" and body["rows"][1]["row"] == 2
    assert _ready(client, group.id, q="foil")["total"] == 1
    assert [r["cth"] for r in _ready(client, group.id, sort="cth", direction="desc")["rows"]] == ["85177990", "76071910", "39269099"]


def test_edit_one_row_writes_the_workbook_and_jobs_see_it(client, db_session):
    group = _setup(client, db_session)
    row = next(r for r in _ready(client, group.id)["rows"] if r["part_code"] == "839599A")
    expected = {k: row[k] for k in ("part_code", "description", "cth")}
    r = client.put(f"{BASE}/{group.id}/rows/{row['row']}", json={
        "expected": expected, "values": {"part_code": "839599A", "description": "COVER FRONT LH NEW", "cth": "8517.79.10"}})
    assert r.status_code == 200, r.text
    assert r.json()["cth"] == "85177910"
    sheet = _sheet(group.id)
    assert list(sheet[0]) == list(HEADERS)
    assert sheet[1] == ["839599A", "COVER FRONT LH NEW", "85177910", "UPDATED"]   # 4th column kept
    assert _lookup(group.id)["839599A"] == "85177910"
    # The same edit again: the row no longer holds what the page showed.
    stale = client.put(f"{BASE}/{group.id}/rows/{row['row']}", json={"expected": expected, "values": expected})
    assert stale.status_code == 409
    backups = list((mm.master_path(group.id).parent / "backups").iterdir())
    assert len(backups) == 1


def test_edit_rules(client, db_session):
    group = _setup(client, db_session)
    rows = _ready(client, group.id)["rows"]
    clip = next(r for r in rows if r["part_code"] == "2827.01")
    expected = {k: clip[k] for k in ("part_code", "description", "cth")}
    bad = client.put(f"{BASE}/{group.id}/rows/{clip['row']}", json={"expected": expected, "values": {**expected, "cth": "3926"}})
    assert bad.status_code == 422 and "8 digits" in bad.json()["detail"]
    dup = client.put(f"{BASE}/{group.id}/rows/{clip['row']}", json={"expected": expected, "values": {**expected, "part_code": "839599a"}})
    assert dup.status_code == 409 and "already in the dump" in dup.json()["detail"]


def test_add_one_row(client, db_session):
    group = _setup(client, db_session)
    _ready(client, group.id)
    r = client.post(f"{BASE}/{group.id}/rows", json={"part_code": "NEW-1", "description": "NEW PART", "cth": "84195099"})
    assert r.status_code == 200, r.text
    assert _ready(client, group.id)["total"] == 4
    assert _sheet(group.id)[-1][:3] == ["NEW-1", "NEW PART", "84195099"]
    assert _lookup(group.id)["NEW-1"] == "84195099"
    again = client.post(f"{BASE}/{group.id}/rows", json={"part_code": "new-1", "description": "x", "cth": "84195099"})
    assert again.status_code == 409


def test_add_batch_from_template(client, db_session):
    group = _setup(client, db_session)
    _ready(client, group.id)
    tpl = openpyxl.load_workbook(io.BytesIO(client.get(f"{BASE}/{group.id}/template").content)).active
    assert [c.value for c in tpl[1]] == ["Part Code", "Description", "CTH"]
    batch = _xlsx([
        ("B-1", "BATCH ONE", "85340000"),
        ("B-2", "BATCH TWO", 85177990),
        ("839599A", "ALREADY THERE", "85177990"),   # existing part code: skipped
        ("B-3", "BAD CTH", "8534"),                  # not 8 digits: skipped
        ("", "", ""),                                # blank: ignored
    ], ("Part Code", "Description", "CTH"))
    r = client.post(f"{BASE}/{group.id}/rows/upload", files={"file": ("add.xlsx", batch, "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 2 and [s["row"] for s in body["skipped"]] == ["Excel row 4", "Excel row 5"]
    assert _ready(client, group.id)["total"] == 5
    wrong = client.post(f"{BASE}/{group.id}/rows/upload",
                        files={"file": ("x.xlsx", _xlsx([("a", "b")], ("Name", "Notes")), "application/octet-stream")})
    assert wrong.status_code == 422


def test_no_full_replace_and_access(client, db_session):
    group = _setup(client, db_session)
    assert client.post(f"{BASE}/{group.id}/upload", files={"file": ("d.xlsx", b"x", "application/octet-stream")}).status_code in (404, 405)
    r = client.get(f"{BASE}/{group.id}/download")
    assert r.status_code == 200
    other = make_tenant(db_session, name=f"O-{uuid.uuid4().hex[:6]}")
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=other, email=f"ta-{uuid.uuid4().hex}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=other, email=f"op-{uuid.uuid4().hex}@example.com")
    assert login(client, ta.email).status_code == 200
    assert client.get(f"{BASE}/{group.id}/rows").status_code == 404
    assert login(client, op.email).status_code == 200
    assert client.get(BASE).status_code == 403


def test_no_dump_yet(client, db_session):
    group = _setup(client, db_session, dump=False)
    body = client.get(f"{BASE}/{group.id}/rows").json()
    assert body["attached"] is False and body["total"] == 0
    r = client.post(f"{BASE}/{group.id}/rows", json={"part_code": "X", "description": "Y", "cth": "84195099"})
    assert r.status_code == 404
