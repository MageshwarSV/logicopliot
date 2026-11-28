"""Renaming a template: only the name changes; a second template of the same customer cannot
take a name already in use (any letter case)."""
import uuid

from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def test_rename_and_duplicate_name_refused(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    a = TemplateGroup(tenant_id=t.id, name="Air import (November 5)", status="approved", mode="Air Import")
    b = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved", mode="Sea Import")
    db_session.add_all([a, b])
    db_session.commit()
    admin = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@x.com")
    assert login(client, admin.email).status_code == 200

    r = client.patch(f"/api/v1/template-groups/{a.id}/name", json={"name": "  Air Import  "})
    assert r.status_code == 200 and r.json()["name"] == "Air Import"
    r = client.patch(f"/api/v1/template-groups/{a.id}/name", json={"name": "sea IMPORT"})
    assert r.status_code == 409
    # renaming to its own name (just changing case) is fine
    assert client.patch(f"/api/v1/template-groups/{a.id}/name", json={"name": "AIR IMPORT"}).status_code == 200
    # another customer may use the same name
    t2 = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    c = TemplateGroup(tenant_id=t2.id, name="Other", status="approved", mode="Sea Import")
    db_session.add(c)
    db_session.commit()
    assert client.patch(f"/api/v1/template-groups/{c.id}/name", json={"name": "Sea Import"}).status_code == 200
