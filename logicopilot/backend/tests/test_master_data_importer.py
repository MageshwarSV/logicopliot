"""ERP Master Data - Importer: one row per importer branch (name + branch + country -> address,
plus GSTIN), kept consistent with the job screen's Name / Branch / Address cascade tables."""
import io
import uuid

import openpyxl

from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    user = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    assert login(client, user.email).status_code == 200
    group = TemplateGroup(tenant_id=tenant.id, name="Air import (November 5)", status="approved")
    db_session.add(group)
    db_session.commit()
    fields = {}
    for key, label in (("name", "ERP Consignee Name"), ("branch", "ERP Importer Address / Branch Code"),
                       ("address", "ERP Branch Address")):
        cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="hardcoded")
        db_session.add(cf)
        db_session.commit()
        fields[key] = cf.id

    def add(key, *vals, value, gstin=None):
        m = list(vals) + [""] * (4 - len(vals))
        db_session.add(Ref(custom_field_id=fields[key], match_value_1=m[0], match_value_2=m[1],
                           match_value_3=m[2], match_value_4=m[3], resolved_value=value, gstin=gstin))

    add("name", "ACME LTD", "INDIA", value="ACME LTD")
    add("branch", "ACME LTD", "CHENNAI", value="CHENNAI")
    add("branch", "ACME LTD", "MUMBAI", value="MUMBAI")
    add("address", "ACME LTD", "CHENNAI", "INDIA", value="1 Anna Salai, Chennai", gstin="33AAACA1234A1Z5")
    add("address", "ACME LTD", "MUMBAI", "INDIA", value="2 Marine Drive, Mumbai")
    add("address", "ZETA GMBH", "BERLIN", "GERMANY", value="Unter den Linden 1, Berlin")
    db_session.commit()
    return group.id, fields


def _rows(db_session, field_id):
    return db_session.query(Ref).filter(Ref.custom_field_id == field_id).all()


def test_templates_with_country_counts(client, db_session):
    gid, _ = _setup(client, db_session)
    body = client.get("/api/v1/master-data/importer-directory").json()
    [tpl] = body["templates"]
    assert tpl["group_id"] == gid
    assert tpl["countries"] == [{"country": "INDIA", "rows": 2, "gstin_rows": 1},
                                {"country": "GERMANY", "rows": 1, "gstin_rows": 0}]
    # The generic category endpoint still works next to it.
    assert client.get("/api/v1/master-data/quotation").status_code == 200


def test_rows_by_country_search_and_sort(client, db_session):
    gid, _ = _setup(client, db_session)
    base = f"/api/v1/master-data/importer-directory/{gid}/rows"
    india = client.get(base, params={"country": "India"}).json()
    assert india["total"] == 2
    assert [r["branch"] for r in india["rows"]] == ["CHENNAI", "MUMBAI"]
    assert india["rows"][0]["gstin"] == "33AAACA1234A1Z5"
    desc = client.get(base, params={"country": "India", "sort": "branch", "direction": "desc"}).json()
    assert [r["branch"] for r in desc["rows"]] == ["MUMBAI", "CHENNAI"]
    # Name only: GSTIN, branch and address text don't match.
    assert client.get(base, params={"country": "India", "q": "acme"}).json()["total"] == 2
    assert client.get(base, params={"country": "India", "q": "33AAACA"}).json()["total"] == 0
    assert client.get(base, params={"country": "India", "q": "marine"}).json()["total"] == 0
    assert client.get(base, params={"country": "India", "q": "mumbai"}).json()["total"] == 0
    assert client.get(base, params={"country": "Germany"}).json()["total"] == 1
    only = client.get(base, params={"country": "India", "gstin_only": "true"}).json()
    assert only["total"] == 1 and only["rows"][0]["branch"] == "CHENNAI"
    assert client.get(base, params={"country": "Germany", "gstin_only": "true"}).json()["total"] == 0
    assert client.get(base).status_code == 422  # country is required


def test_add_writes_all_three_tables(client, db_session):
    gid, f = _setup(client, db_session)
    r = client.post(f"/api/v1/master-data/importer-directory/{gid}/rows", json={
        "country": "India", "name": "New Co Pvt Ltd", "branch": "Pune", "gstin": "27aabcn1234a1z5 ",
        "address": "Hinjewadi, Pune"})
    assert r.status_code == 201
    assert r.json()["gstin"] == "27AABCN1234A1Z5" and r.json()["name"] == "NEW CO PVT LTD"
    assert any(x.match_value_1 == "NEW CO PVT LTD" and x.match_value_2 == "INDIA" for x in _rows(db_session, f["name"]))
    assert any(x.match_value_1 == "NEW CO PVT LTD" and x.resolved_value == "Pune" for x in _rows(db_session, f["branch"]))

    dup = client.post(f"/api/v1/master-data/importer-directory/{gid}/rows", json={
        "country": "INDIA", "name": "new co pvt ltd", "branch": "PUNE", "address": "elsewhere"})
    assert dup.status_code == 409


def test_edit_address_and_gstin(client, db_session):
    gid, _ = _setup(client, db_session)
    base = f"/api/v1/master-data/importer-directory/{gid}/rows"
    row = client.get(base, params={"country": "India"}).json()["rows"][1]
    r = client.patch(f"{base}/{row['id']}", json={"address": "3 New Road, Mumbai", "gstin": "27AAACA1234A1Z5"})
    assert r.status_code == 200
    assert r.json()["address"] == "3 New Road, Mumbai" and r.json()["gstin"] == "27AAACA1234A1Z5"
    assert client.patch(f"{base}/{row['id']}", json={"gstin": ""}).json()["gstin"] is None
    assert client.patch(f"{base}/{row['id']}", json={"address": " "}).status_code == 422


def _active(db_session, field_id):
    db_session.expire_all()
    return {(x.match_value_1, x.match_value_2): x.is_active for x in _rows(db_session, field_id)}


def test_inactive_cascades_and_reactivates(client, db_session):
    gid, f = _setup(client, db_session)
    base = f"/api/v1/master-data/importer-directory/{gid}/rows"
    chennai, mumbai = client.get(base, params={"country": "India"}).json()["rows"]
    r = client.put(f"{base}/{chennai['id']}/active", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    # CHENNAI branch row off; name row stays on (MUMBAI still active).
    assert _active(db_session, f["branch"]) == {("ACME LTD", "CHENNAI"): False, ("ACME LTD", "MUMBAI"): True}
    assert _active(db_session, f["name"]) == {("ACME LTD", "INDIA"): True}
    client.put(f"{base}/{mumbai['id']}/active", json={"is_active": False})
    assert _active(db_session, f["name"]) == {("ACME LTD", "INDIA"): False}
    # Still listed on the master-data screen, just marked inactive; never deleted.
    assert client.get(base, params={"country": "India"}).json()["total"] == 2
    assert client.delete(f"{base}/{chennai['id']}").status_code == 405
    # Re-adding the same key is refused with a pointer to reactivate it.
    dup = client.post(base, json={"country": "India", "name": "ACME LTD", "branch": "CHENNAI", "address": "x"})
    assert dup.status_code == 409 and "Inactive" in dup.json()["detail"]
    client.put(f"{base}/{chennai['id']}/active", json={"is_active": True})
    assert _active(db_session, f["branch"])[("ACME LTD", "CHENNAI")] is True
    assert _active(db_session, f["name"]) == {("ACME LTD", "INDIA"): True}


def test_inactive_rows_hidden_from_job_lookups(client, db_session):
    from app.core.reference_cache import lookup_reference, reference_map_for

    gid, f = _setup(client, db_session)
    base = f"/api/v1/master-data/importer-directory/{gid}/rows"
    chennai = client.get(base, params={"country": "India"}).json()["rows"][0]
    key = ["ACME LTD", "CHENNAI", "INDIA"]
    assert lookup_reference(db_session, f["address"], key) == "1 Anna Salai, Chennai"
    client.put(f"{base}/{chennai['id']}/active", json={"is_active": False})
    db_session.expire_all()
    assert lookup_reference(db_session, f["address"], key) is None
    assert ("ACME LTD", "CHENNAI", "INDIA", "") not in reference_map_for(db_session, f["address"])


def test_upload_by_header_fills_gstin_and_reports_conflicts(client, db_session):
    gid, f = _setup(client, db_session)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Country", "Organization", "Branch Name", "Address", "GSTIN"])  # the original Excel's layout
    ws.append(["India", "ACME LTD", "Mumbai", "2 Marine Drive, Mumbai", "27AAACA1234A1Z5"])   # fills GSTIN
    ws.append(["India", "ACME LTD", "Chennai", "1 Anna Salai, Chennai", "33ZZZZZ9999Z1Z5"])   # GSTIN differs
    ws.append(["Germany", "Zeta GmbH", "Berlin", "Somewhere else", None])                    # address differs
    ws.append(["India", "(Brand New) Co", "Delhi", "CP, Delhi", None])                        # new
    ws.append(["", "No Country", "X", "Y", None])                                             # skipped
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"/api/v1/master-data/importer-directory/{gid}/upload",
                    files={"file": ("x.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["rows_added"], body["gstin_filled"], body["rows_skipped"]) == (1, 1, 1)
    assert sorted(c["field"] for c in body["conflicts"]) == ["address", "gstin"]
    assert any(x.match_value_1 == "BRAND NEW) CO" for x in _rows(db_session, f["branch"]))


def test_upload_needs_headings(client, db_session):
    gid, _ = _setup(client, db_session)
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b", "c"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"/api/v1/master-data/importer-directory/{gid}/upload",
                    files={"file": ("x.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 422


def test_export_selected_country(client, db_session):
    gid, _ = _setup(client, db_session)
    r = client.get(f"/api/v1/master-data/importer-directory/{gid}/export", params={"country": "India"})
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[1]] == ["Country", "ERP Importer Name", "Branch", "GSTIN", "Branch Address"]
    assert ws.max_row == 3
    r = client.get(f"/api/v1/master-data/importer-directory/{gid}/export", params={"country": "India", "gstin_only": "true"})
    assert openpyxl.load_workbook(io.BytesIO(r.content)).active.max_row == 2


def test_other_tenant_template_is_404(client, db_session):
    gid, _ = _setup(client, db_session)
    other = make_tenant(db_session, name=f"O-{uuid.uuid4().hex[:6]}")
    user = make_user(db_session, role=TENANT_ADMIN, tenant=other, email=f"ta-{uuid.uuid4().hex}@example.com")
    assert login(client, user.email).status_code == 200
    assert client.get(f"/api/v1/master-data/importer-directory/{gid}/rows", params={"country": "India"}).status_code == 404
