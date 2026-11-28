"""Knowledge Hub: Super Admin only; written pages from docs/knowledge, generated sections from
the code; settings never expose a value."""
import uuid

from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _sa(client, db_session):
    u = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@x.com")
    assert login(client, u.email).status_code == 200


def test_only_super_admin(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    u = make_user(db_session, role=TENANT_ADMIN, tenant=t, email=f"ta-{uuid.uuid4().hex[:6]}@x.com")
    assert login(client, u.email).status_code == 200
    assert client.get("/api/v1/knowledge/home").status_code == 403


def test_home_pages_and_viewer(client, db_session):
    _sa(client, db_session)
    h = client.get("/api/v1/knowledge/home").json()
    assert h["counts"]["documents"] >= 1 and h["counts"]["db_tables"] > 10 and h["counts"]["api_endpoints"] > 10
    assert 0 <= h["completeness"] <= 100 and h["modules"]
    items = client.get("/api/v1/knowledge/pages").json()["items"]
    slug = next(p["slug"] for p in items if p["slug"] == "architecture/how-cargora-works")
    p = client.get(f"/api/v1/knowledge/pages/{slug}").json()
    assert p["body"] and p["category"] == "architecture"
    assert client.get("/api/v1/knowledge/pages/nope/missing").status_code == 404


def test_generated_sections_and_search(client, db_session):
    _sa(client, db_session)
    tables = client.get("/api/v1/knowledge/database").json()["tables"]
    assert any(t["table"] == "users" for t in tables)
    apis = client.get("/api/v1/knowledge/apis").json()["endpoints"]
    assert any(a["path"] == "/api/v1/knowledge/home" for a in apis)
    settings = client.get("/api/v1/knowledge/settings").json()["settings"]
    assert settings and all(set(s) == {"name", "type", "set"} for s in settings)
    assert client.get("/api/v1/knowledge/changes").status_code == 200
    res = client.get("/api/v1/knowledge/search", params={"q": "users"}).json()["results"]
    assert any(r["type"] == "table" for r in res)
