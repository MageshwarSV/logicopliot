"""Indian Customs Tariff (ICEGATE) master - phase 1: load, replace, search, check."""
import io
import os

import openpyxl
import pytest

from app.core import customs_tariff as ct
from app.models.customs_tariff import CustomsTariff
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

REAL_FILE = os.path.expanduser("~/Desktop/axioraprj/CTH Tariff.new.xlsx")


def _xlsx(rows, header=("CTH", "Rate", "Unit", "Preferential Rate")) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _codes(n, start=10000000):
    return [(str(start + i), "10%", "KGS", "--NIL--") for i in range(n)]


def test_clean_cth():
    assert ct.clean_cth("0101 21 00") == "01012100"
    assert ct.clean_cth("8517.79.90") == "85177990"
    assert ct.clean_cth(1012100) == "01012100"          # number cell lost its leading zero
    assert ct.clean_cth(85177990.0) == "85177990"
    assert ct.clean_cth("1012100") == ""                # 7-digit TEXT: not guessed
    assert ct.clean_cth("8517") == ""
    assert ct.clean_cth("85AB7990") == ""
    assert ct.clean_cth(None) == ""


def test_parse_finds_columns_and_skips_bad_rows():
    rows = _codes(1200) + [("12345", "x", "y", "z"), ("10000001", "99%", "NOS", "-")]
    p = ct.parse_file(_xlsx(rows), "t.xlsx")
    assert len(p.rows) == 1200
    assert p.columns == ["rate", "unit", "preferential_rate"]     # no description column
    reasons = sorted(s["reason"] for s in p.skipped)
    assert reasons[0] == "not an 8-digit CTH"
    assert "repeated" in reasons[1]
    assert p.rows["10000001"] == {"description": None, "rate": "10%", "unit": "KGS", "preferential_rate": "--NIL--"}


def test_parse_refuses_wrong_files():
    with pytest.raises(ct.TariffError, match="No CTH column"):
        ct.parse_file(_xlsx(_codes(1200), header=("Code", "Rate")), "t.xlsx")
    with pytest.raises(ct.TariffError, match="whole list"):
        ct.parse_file(_xlsx(_codes(50)), "t.xlsx")
    with pytest.raises(ct.TariffError, match="Excel"):
        ct.parse_file(b"abc", "t.pdf")


def test_csv_and_description_column():
    lines = ["Tariff Item,Description of goods,Unit"] + [f"{10000000 + i},Goods {i},KGS" for i in range(1100)]
    p = ct.parse_file("\n".join(lines).encode(), "t.csv")
    assert p.columns == ["description", "unit"]
    assert p.rows["10000005"]["description"] == "Goods 5"


def test_apply_replaces_without_deleting(db_session):
    first = ct.parse_file(_xlsx(_codes(1100)), "v1.xlsx")
    r1 = ct.apply_upload(db_session, first, "v1.xlsx", "admin@x")
    assert (r1["added"], r1["deactivated"]) == (1100, 0)
    assert ct.is_allowed(db_session, "10000000")

    # v2: drops the first 10 codes, changes one rate, adds 5 new ones.
    rows = _codes(1090, start=10000010) + [(str(20000000 + i), "5%", "NOS", "-") for i in range(5)]
    rows[0] = ("10000010", "15%", "KGS", "--NIL--")
    second = ct.parse_file(_xlsx(rows), "v2.xlsx")
    pre = ct.preview_upload(db_session, second)
    assert (pre["added"], pre["changed"], pre["deactivated"]) == (5, 1, 10)
    assert db_session.query(CustomsTariff).count() == 1100        # preview writes nothing

    ct.apply_upload(db_session, second, "v2.xlsx", "admin@x")
    gone = db_session.query(CustomsTariff).filter_by(cth="10000000").one()
    assert gone.is_active is False and gone.deactivated_at is not None   # kept, not deleted
    assert not ct.is_allowed(db_session, "10000000")
    assert ct.is_allowed(db_session, "20000004")
    assert db_session.query(CustomsTariff).filter_by(cth="10000010").one().rate == "15%"

    # v3 brings a dropped code back.
    third = ct.parse_file(_xlsx(rows + [("10000000", "10%", "KGS", "--NIL--")]), "v3.xlsx")
    r3 = ct.apply_upload(db_session, third, "v3.xlsx", None)
    assert r3["reactivated"] == 1 and ct.is_allowed(db_session, "10000000")


def test_large_removal_is_flagged(db_session):
    ct.apply_upload(db_session, ct.parse_file(_xlsx(_codes(2000)), "a.xlsx"), "a.xlsx", None)
    pre = ct.preview_upload(db_session, ct.parse_file(_xlsx(_codes(1500)), "b.xlsx"))
    assert pre["deactivated"] == 500 and pre["large_removal"] is True


def test_search(db_session):
    rows = _codes(1000) + [("85177990", "20%", "NOS", "-"), ("85177910", "20%", "KGS", "-")]
    ct.apply_upload(db_session, ct.parse_file(_xlsx(rows), "a.xlsx"), "a.xlsx", None)
    assert ct.search(db_session, "8517 79")["total"] == 2
    assert ct.search(db_session, "8517", page_size=1)["rows"][0]["cth"] == "85177910"
    assert ct.search(db_session, "nos")["total"] == 1


def test_api_roles(client, db_session):
    t = make_tenant(db_session)
    sa = make_user(db_session, role=SUPER_ADMIN)
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=t)
    op = make_user(db_session, role=OPERATOR, tenant=t)
    files = {"file": ("v1.xlsx", _xlsx(_codes(1100)), "application/octet-stream")}

    login(client, ta.email)
    assert client.post("/api/v1/customs-tariff/upload", files=files).status_code == 403
    login(client, sa.email)
    assert client.post("/api/v1/customs-tariff/upload/preview", files=files).json()["added"] == 1100
    assert client.get("/api/v1/customs-tariff/summary").json()["active"] == 0
    assert client.post("/api/v1/customs-tariff/upload", files=files).json()["added"] == 1100
    assert client.get("/api/v1/customs-tariff/uploads").json()["uploads"][0]["uploaded_by"] == sa.email
    bad = client.post("/api/v1/customs-tariff/upload", files={"file": ("x.xlsx", _xlsx(_codes(3)), "x")})
    assert bad.status_code == 422

    login(client, op.email)                    # read-only for everyone signed in
    assert client.get("/api/v1/customs-tariff/codes", params={"q": "1000000"}).json()["total"] == 10
    assert client.get("/api/v1/customs-tariff/codes/10000001").json()["unit"] == "KGS"
    assert client.get("/api/v1/customs-tariff/codes/99999999").status_code == 404
    assert client.get("/api/v1/customs-tariff/download").status_code == 403
    login(client, ta.email)
    dl = client.get("/api/v1/customs-tariff/download")
    ws = openpyxl.load_workbook(io.BytesIO(dl.content)).active
    assert ws.max_row == 1101 and ws["A2"].value == "10000000"


@pytest.mark.skipif(not os.path.exists(REAL_FILE), reason="the real tariff file is only on the developer's machine")
def test_real_icegate_file(db_session):
    with open(REAL_FILE, "rb") as f:
        parsed = ct.parse_file(f.read(), "CTH Tariff.new.xlsx")
    assert len(parsed.rows) == 12742
    # Two typo rows in the 2026-11-26 file: a chapter line "10" and a 7-digit "6001090".
    assert sorted(s["value"] for s in parsed.skipped) == ["10", "6001090"]
    assert parsed.rows["01012100"]["unit"] == "NOS"
    ct.apply_upload(db_session, parsed, "CTH Tariff.new.xlsx", None)
    assert ct.is_allowed(db_session, "0101 21 00")
