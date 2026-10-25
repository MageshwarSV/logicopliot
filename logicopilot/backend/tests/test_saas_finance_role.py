"""The SaaS Finance role: cross-tenant, read-only over billing/invoices, no tenant/user
management - created by Super Admin alone, same shape as Admin (see app/models/user.py)."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session, email="sa-finrole@example.com"):
    sa = make_user(db_session, role="super_admin", email=email)
    login(client, sa.email)
    return sa


# ---------------------------------------------------------------- creation

def test_super_admin_creates_a_saas_finance_user(client, db_session):
    _login_super_admin(client, db_session)
    resp = client.post("/api/v1/users", json={
        "email": "new-finance@example.com", "password": "TestPass123",
        "full_name": "New Finance", "role": "saas_finance",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "saas_finance"
    assert body["tenant_id"] is None


def test_tenant_admin_cannot_create_a_saas_finance_user(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-finrole@example.com")
    login(client, ta.email)
    resp = client.post("/api/v1/users", json={
        "email": "nope-finance@example.com", "password": "TestPass123",
        "full_name": "Nope", "role": "saas_finance",
    })
    assert resp.status_code == 403


def test_saas_finance_creation_ignores_a_submitted_tenant_id(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session, email="sa-finrole2@example.com")
    resp = client.post("/api/v1/users", json={
        "email": "finance-ignoretenant@example.com", "password": "TestPass123",
        "full_name": "Finance", "role": "saas_finance", "tenant_id": tenant.id,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["tenant_id"] is None


# ---------------------------------------------------------------- DB constraint

def test_db_rejects_a_saas_finance_row_with_a_tenant_id(db_session):
    tenant = make_tenant(db_session)
    with pytest.raises(IntegrityError):
        make_user(db_session, role="saas_finance", tenant=tenant, email="bad-finance@example.com")


# ---------------------------------------------------------------- update/delete hierarchy

def test_super_admin_can_update_a_saas_finance_user(client, db_session):
    _login_super_admin(client, db_session, email="sa-finrole3@example.com")
    fin = make_user(db_session, role="saas_finance", email="fin-update@example.com")
    resp = client.patch(f"/api/v1/users/{fin.id}", json={"full_name": "Renamed Finance"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Renamed Finance"


def test_tenant_admin_cannot_update_a_saas_finance_user(client, db_session):
    tenant = make_tenant(db_session)
    fin = make_user(db_session, role="saas_finance", email="fin-noupdate@example.com")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-finrole2@example.com")
    login(client, ta.email)
    resp = client.patch(f"/api/v1/users/{fin.id}", json={"full_name": "Hacked"})
    assert resp.status_code == 404  # scoped_query never finds a cross-tenant user for a tenant_admin


def test_super_admin_can_delete_a_saas_finance_user(client, db_session):
    _login_super_admin(client, db_session, email="sa-finrole4@example.com")
    fin = make_user(db_session, role="saas_finance", email="fin-delete@example.com")
    resp = client.delete(f"/api/v1/users/{fin.id}")
    assert resp.status_code == 204, resp.text
    # Session.get() would return the still-cached pre-delete object from this test's own
    # identity map without re-querying - a plain Query always hits the DB.
    assert db_session.query(User).filter(User.id == fin.id).first() is None
