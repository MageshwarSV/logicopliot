"""Tenant Admin -> Master Data -> ERP Master Data (Importer / Quotation): the existing importer
and quotation reference tables, paged, searchable and editable by the tenant's own admin."""
import io
import uuid

import openpyxl

from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _admin(client, db_session, tenant=None):
    tenant = tenant or make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    user = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    assert login(client, user.email).status_code == 200
    return tenant


def _field(db_session, tenant, label, group_name="Sea import", rows=()):
    group = db_session.query(TemplateGroup).filter_by(tenant_id=tenant.id, name=group_name).first()
    if group is None:
        group = TemplateGroup(tenant_id=tenant.id, name=group_name, status="approved")
        db_session.add(group)
        db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="hardcoded")
    db_session.add(cf)
    db_session.commit()
    for match, value in rows:
        m = list(match) + [""] * (4 - len(match))
        db_session.add(CustomFieldReferenceValue(
            custom_field_id=cf.id, match_value_1=m[0], match_value_2=m[1], match_value_3=m[2],
            match_value_4=m[3], resolved_value=value))
    db_session.commit()
    return cf


def test_lists_only_this_categorys_tables_with_counts(client, db_session):
    tenant = _admin(client, db_session)
    name = _field(db_session, tenant, "ERP Consignee Name", rows=[(["ACME", "INDIA"], "ACME LTD")])
    _field(db_session, tenant, "ERP Branch Address")
    _field(db_session, tenant, "Erp Entry Name")
    _field(db_session, tenant, "CTH", rows=[(["X"], "1")])

    r = client.get("/api/v1/master-data/importer")
    assert r.status_code == 200
    body = r.json()
    assert [t["key"] for t in body["tables"]] == ["name", "branch", "address"]
    [tpl] = body["templates"]
    assert tpl["name"] == "Sea import"
    assert set(tpl["tables"]) == {"name", "address"}
    assert tpl["tables"]["name"] == {"field_id": name.id, "rows": 1}

    q = client.get("/api/v1/master-data/quotation").json()
    assert set(q["templates"][0]["tables"]) == {"name"}
    assert client.get("/api/v1/master-data/other").status_code == 404


def test_rows_paged_and_searched(client, db_session):
    tenant = _admin(client, db_session)
    rows = [([f"IMPORTER {i:02d}", "INDIA"], f"Importer {i:02d} Pvt Ltd") for i in range(30)]
    rows.append((["100%_SPECIAL", "CHINA"], "Special"))
    cf = _field(db_session, tenant, "ERP Consignee Name", rows=rows)
    base = f"/api/v1/master-data/importer/fields/{cf.id}/rows"

    page2 = client.get(base, params={"page": 2, "page_size": 10}).json()
    assert page2["total"] == 31 and len(page2["rows"]) == 10
    assert page2["rows"][0]["match_values"][0] == "IMPORTER 09"

    hit = client.get(base, params={"q": "importer 2"}).json()
    assert hit["total"] == 10
    assert client.get(base, params={"q": "china"}).json()["total"] == 1
    # % and _ are literal, not wildcards.
    assert client.get(base, params={"q": "0%_s"}).json()["total"] == 1
    assert client.get(base, params={"q": "r_0"}).json()["total"] == 0


def test_add_edit_delete(client, db_session):
    tenant = _admin(client, db_session)
    cf = _field(db_session, tenant, "quotation_address", rows=[(["AIT", "CHENNAI"], "Guindy")])
    base = f"/api/v1/master-data/quotation/fields/{cf.id}/rows"

    r = client.post(base, json={"rows": [
        {"match_values": ["AIT", "Mumbai"], "resolved_value": "Chakala"},
        {"match_values": ["AIT", "Chennai"], "resolved_value": "Somewhere else"},
    ]})
    assert r.status_code == 200
    assert r.json()["rows_added"] == 1
    assert len(r.json()["conflicts"]) == 1  # existing key, different value: reported, not overwritten

    rows = client.get(base).json()["rows"]
    chennai = next(x for x in rows if x["match_values"][1] == "CHENNAI")
    assert chennai["resolved_value"] == "Guindy"

    r = client.patch(f"{base}/{chennai['id']}", json={"resolved_value": "8th floor, RR Tower, Guindy"})
    assert r.status_code == 200 and r.json()["resolved_value"] == "8th floor, RR Tower, Guindy"

    assert client.delete(f"{base}/{chennai['id']}").status_code == 204
    assert client.get(base).json()["total"] == 1


def test_upload_template_and_export(client, db_session):
    tenant = _admin(client, db_session)
    cf = _field(db_session, tenant, "ERP Importer Address / Branch Code", rows=[(["ACME LTD", "MAIN"], "MAIN")])
    base = f"/api/v1/master-data/importer/fields/{cf.id}"

    tpl = openpyxl.load_workbook(io.BytesIO(client.get(f"{base}/template").content))
    assert [c.value for c in tpl.active[1]][:2] == ["ERP Importer Name", "Branch"]
    assert tpl.active[1][4].value == "ERP Branch Code"

    wb = openpyxl.Workbook()
    wb.active.append(["h1", "h2", "", "", "h5"])
    wb.active.append(["ACME LTD", "Chennai", "", "", "CHENNAI-2"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"{base}/upload", files={"file": ("b.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200 and r.json()["rows_added"] == 1

    exported = openpyxl.load_workbook(io.BytesIO(client.get(f"{base}/export").content)).active
    assert exported.max_row == 3


def test_other_tenant_and_other_fields_unreachable(client, db_session):
    other = make_tenant(db_session, name=f"O-{uuid.uuid4().hex[:6]}")
    foreign = _field(db_session, other, "ERP Consignee Name", rows=[(["X", "INDIA"], "X")])
    tenant = _admin(client, db_session)
    cth = _field(db_session, tenant, "CTH", rows=[(["X"], "1")])
    name = _field(db_session, tenant, "ERP Consignee Name")

    assert client.get(f"/api/v1/master-data/importer/fields/{foreign.id}/rows").status_code == 404
    assert client.get(f"/api/v1/master-data/importer/fields/{cth.id}/rows").status_code == 404
    # An importer table is not reachable under the quotation category.
    assert client.get(f"/api/v1/master-data/quotation/fields/{name.id}/rows").status_code == 404


def test_operator_forbidden(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email=f"op-{uuid.uuid4().hex}@example.com")
    assert login(client, op.email).status_code == 200
    assert client.get("/api/v1/master-data/importer").status_code == 403
