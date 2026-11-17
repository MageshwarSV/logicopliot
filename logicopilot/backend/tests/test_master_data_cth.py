"""CTH Master: list the customer's CTH dump (material master workbook) and replace it safely
with a new one - a bad upload never takes the working dump away from jobs."""
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


@pytest.fixture(autouse=True)
def _uploads_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    mm.clear_cache()


def _xlsx(rows, headers=("Material", "Material Description", "Comm./imp. code no.")) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.append(list(headers))
    for r in rows:
        wb.active.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _setup(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    admin = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="approved", mode="Sea Import")
    other = TemplateGroup(tenant_id=tenant.id, name="Sea export", status="approved", mode="Sea Export")
    db_session.add_all([group, other])
    db_session.commit()
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH", kind="lookup"))
    db_session.commit()
    assert login(client, admin.email).status_code == 200
    return tenant, group


def _upload(client, gid, content, name="JUNE DUMP.xlsx"):
    r = client.post(f"{BASE}/{gid}/upload", files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == 202, r.text
    for _ in range(100):
        if not client.get(f"{BASE}/{gid}/rows").json()["checking_upload"]:
            return
        time.sleep(0.05)
    raise AssertionError("upload check never finished")


def _ready_rows(client, gid, **params):
    for _ in range(100):
        body = client.get(f"{BASE}/{gid}/rows", params=params).json()
        if body["ready"]:
            return body
        time.sleep(0.05)
    raise AssertionError("rows never became ready")


def test_only_templates_with_a_cth_field_or_dump_are_listed(client, db_session):
    _, group = _setup(client, db_session)
    [tpl] = client.get(BASE).json()["templates"]
    assert tpl["group_id"] == group.id and tpl["attached"] is False and tpl["rows"] is None


def test_upload_list_search_sort_and_lookup_uses_it(client, db_session):
    _, group = _setup(client, db_session)
    _upload(client, group.id, _xlsx([
        ("839599A", "COVER FRONT LH", "8517 79 90"),
        ("2827.01", "PLASTIC CLIP", 39269099),
        ("", "AL FOIL 5U", "76071910"),
    ]))
    body = _ready_rows(client, group.id)
    assert body["file_name"] == "JUNE DUMP.xlsx" and body["total"] == 3
    assert [r["part_code"] for r in body["rows"]] == ["2827.01", "839599A", ""]  # blanks last
    assert body["rows"][1]["cth"] == "85177990" and body["rows"][1]["cth_as_written"] == "8517 79 90"
    assert _ready_rows(client, group.id, q="foil")["total"] == 1
    assert _ready_rows(client, group.id, q="85177990")["total"] == 1
    desc = _ready_rows(client, group.id, sort="cth", direction="desc")["rows"]
    assert [r["cth"] for r in desc] == ["85177990", "76071910", "39269099"]
    # Jobs read the same file, unchanged.
    for _ in range(100):
        if not mm.is_building(group.id):
            break
        time.sleep(0.05)
    assert mm.load_master(group.id)["839599A"] == "85177990"


def test_bad_upload_keeps_the_working_dump(client, db_session):
    _, group = _setup(client, db_session)
    _upload(client, group.id, _xlsx([("839599A", "COVER", "85177990")]))
    _upload(client, group.id, _xlsx([("a", "b")], headers=("Name", "Notes")), name="wrong.xlsx")
    body = _ready_rows(client, group.id)
    assert body["file_name"] == "JUNE DUMP.xlsx" and body["total"] == 1
    assert "wrong.xlsx" in body["upload_error"] and "previous dump is still in use" in body["upload_error"]
    # A good upload afterwards clears the error and replaces the dump.
    _upload(client, group.id, _xlsx([("X1", "NEW PART", "84195099"), ("X2", "OTHER", "84195099")]), name="NOV DUMP.xlsx")
    body = _ready_rows(client, group.id)
    assert body["upload_error"] is None and body["file_name"] == "NOV DUMP.xlsx" and body["total"] == 2


def test_download_and_template(client, db_session):
    _, group = _setup(client, db_session)
    assert client.get(f"{BASE}/{group.id}/download").status_code == 404
    content = _xlsx([("839599A", "COVER", "85177990")])
    _upload(client, group.id, content)
    r = client.get(f"{BASE}/{group.id}/download")
    assert r.status_code == 200 and r.content == content
    ws = openpyxl.load_workbook(io.BytesIO(client.get(f"{BASE}/{group.id}/template").content)).active
    assert [c.value for c in ws[1]] == ["Material", "Material Description", "Comm./imp. code no."]


def test_wrong_file_type_and_other_tenant(client, db_session):
    _, group = _setup(client, db_session)
    r = client.post(f"{BASE}/{group.id}/upload", files={"file": ("dump.xls", b"x", "application/octet-stream")})
    assert r.status_code == 422
    other = make_tenant(db_session, name=f"O-{uuid.uuid4().hex[:6]}")
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=other, email=f"ta-{uuid.uuid4().hex}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=other, email=f"op-{uuid.uuid4().hex}@example.com")
    assert login(client, ta.email).status_code == 200
    assert client.get(f"{BASE}/{group.id}/rows").status_code == 404
    assert login(client, op.email).status_code == 200
    assert client.get(BASE).status_code == 403
