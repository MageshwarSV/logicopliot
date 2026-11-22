"""ERP Master Data - Quotation: one row per ERP quotation name + branch with its address, kept
consistent with the job screen's Erp Entry Name / branch_name / quotation_address tables."""
import io
import uuid

import openpyxl

from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

AIT = "AIT WORLDWIDE LOGISTICS INDIA PRIVATE LIMITED"


def _setup(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    user = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    assert login(client, user.email).status_code == 200
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="approved")
    db_session.add(group)
    db_session.commit()
    f = {}
    for key, label in (("entry", "Erp Entry Name"), ("branch", "branch_name"), ("address", "quotation_address")):
        cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="hardcoded")
        db_session.add(cf)
        db_session.commit()
        f[key] = cf.id

    def add(key, m1, m2, value):
        db_session.add(Ref(custom_field_id=f[key], match_value_1=m1, match_value_2=m2, match_value_3="",
                           match_value_4="", resolved_value=value))

    # Mixed case, as Sea import really stores it.
    add("entry", "AIT WORLDWIDE LOGISTICS", "", AIT)
    add("branch", AIT, "", "Bangalore")
    add("branch", AIT, "", "CHENNAI")
    add("address", AIT, "Bangalore", "Manyata Tech Park, Bangalore")
    add("address", AIT, "CHENNAI", "RR Tower, Guindy, Chennai")
    add("address", "ZETA LOGISTICS", "MUMBAI", "Chakala, Mumbai")
    db_session.commit()
    return group.id, f


def _active(db_session, field_id):
    db_session.expire_all()
    return {(r.match_value_1, r.resolved_value): r.is_active
            for r in db_session.query(Ref).filter(Ref.custom_field_id == field_id).all()}


def test_templates_and_rows_sorted_and_searched_by_name(client, db_session):
    gid, _ = _setup(client, db_session)
    [tpl] = client.get("/api/v1/master-data/quotation-directory").json()["templates"]
    assert tpl == {"group_id": gid, "name": "Sea import", "rows": 3}
    base = f"/api/v1/master-data/quotation-directory/{gid}/rows"
    body = client.get(base).json()
    assert body["total"] == 3
    assert [(r["name"], r["branch"]) for r in body["rows"]] == [(AIT, "Bangalore"), (AIT, "CHENNAI"), ("ZETA LOGISTICS", "MUMBAI")]
    by_branch = client.get(base, params={"sort": "branch", "direction": "desc"}).json()["rows"]
    assert [r["branch"] for r in by_branch] == ["MUMBAI", "CHENNAI", "Bangalore"]
    assert client.get(base, params={"q": "ait"}).json()["total"] == 2
    assert client.get(base, params={"q": "guindy"}).json()["total"] == 0  # name only


def test_add_writes_all_three_and_refuses_case_insensitive_duplicate(client, db_session):
    gid, f = _setup(client, db_session)
    base = f"/api/v1/master-data/quotation-directory/{gid}/rows"
    r = client.post(base, json={"name": "New Freight (Chennai)", "branch": "Ambattur", "address": "Ambattur, Chennai"})
    assert r.status_code == 201
    assert r.json()["name"] == "NEW FREIGHT (CHENNAI)" and r.json()["branch"] == "AMBATTUR"
    assert _active(db_session, f["branch"])[("NEW FREIGHT (CHENNAI)", "Ambattur")] is True
    assert ("NEW FREIGHT (CHENNAI", "New Freight (Chennai)") in _active(db_session, f["entry"])
    # Existing AIT branch added again in a different case: refused, nothing written.
    dup = client.post(base, json={"name": AIT.lower(), "branch": "BANGALORE", "address": "x"})
    assert dup.status_code == 409
    # A new AIT branch: address + branch rows added, no second entry row for AIT.
    assert client.post(base, json={"name": AIT, "branch": "Pune", "address": "Pune"}).status_code == 201
    assert len([k for k in _active(db_session, f["entry"]) if k[1] == AIT]) == 1


def test_edit_address(client, db_session):
    gid, _ = _setup(client, db_session)
    base = f"/api/v1/master-data/quotation-directory/{gid}/rows"
    row = client.get(base).json()["rows"][0]
    r = client.patch(f"{base}/{row['id']}", json={"address": "New address"})
    assert r.status_code == 200 and r.json()["address"] == "New address"
    assert client.patch(f"{base}/{row['id']}", json={"address": "  "}).status_code == 422


def test_inactive_cascades_and_reactivates(client, db_session):
    gid, f = _setup(client, db_session)
    base = f"/api/v1/master-data/quotation-directory/{gid}/rows"
    bangalore, chennai, _ = client.get(base).json()["rows"]
    client.put(f"{base}/{bangalore['id']}/active", json={"is_active": False})
    assert _active(db_session, f["branch"]) == {(AIT, "Bangalore"): False, (AIT, "CHENNAI"): True}
    assert _active(db_session, f["entry"]) == {("AIT WORLDWIDE LOGISTICS", AIT): True}
    client.put(f"{base}/{chennai['id']}/active", json={"is_active": False})
    assert _active(db_session, f["entry"]) == {("AIT WORLDWIDE LOGISTICS", AIT): False}
    assert client.get(base).json()["total"] == 3  # still listed, never deleted
    assert client.delete(f"{base}/{chennai['id']}").status_code == 405
    dup = client.post(base, json={"name": AIT, "branch": "chennai", "address": "x"})
    assert dup.status_code == 409 and "Inactive" in dup.json()["detail"]
    client.put(f"{base}/{chennai['id']}/active", json={"is_active": True})
    assert _active(db_session, f["branch"])[(AIT, "CHENNAI")] is True
    assert _active(db_session, f["entry"]) == {("AIT WORLDWIDE LOGISTICS", AIT): True}


def test_export(client, db_session):
    gid, _ = _setup(client, db_session)
    ws = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/v1/master-data/quotation-directory/{gid}/export").content)).active
    assert [c.value for c in ws[1]] == ["ERP Quotation Name", "Quotation Branch", "Quotation Address", "Status"]
    assert ws.max_row == 4


def test_other_tenant_and_operator_blocked(client, db_session):
    gid, _ = _setup(client, db_session)
    other = make_tenant(db_session, name=f"O-{uuid.uuid4().hex[:6]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=other, email=f"ta-{uuid.uuid4().hex}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=other, email=f"op-{uuid.uuid4().hex}@example.com")
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=other, email=f"ta2-{uuid.uuid4().hex}@example.com")
    assert login(client, ta.email).status_code == 200
    assert client.get(f"/api/v1/master-data/quotation-directory/{gid}/rows").status_code == 404
    assert login(client, op.email).status_code == 200
    assert client.get("/api/v1/master-data/quotation-directory").status_code == 403
