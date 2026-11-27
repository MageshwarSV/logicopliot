"""Super Admin switches a customer off / on: login, sessions, mail, ERP logins and billing."""
import uuid
from datetime import date

from app.models.connected_mailbox import ConnectedMailbox
from app.models.tenant import Tenant
from app.models.user import OPERATOR, SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _e(p):
    return f"{p}-{uuid.uuid4().hex[:8]}@example.com"


def test_disable_blocks_login_and_open_sessions_then_enable_restores(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=t, email=_e("ta"))
    op = make_user(db_session, role=OPERATOR, tenant=t, email=_e("op"))
    assert login(client, op.email).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    t.is_active = False
    db_session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401            # open session ends
    r = login(client, op.email)
    assert r.status_code == 401 and "switched off" in r.json()["detail"]
    t.is_active = True
    db_session.commit()
    assert login(client, op.email).status_code == 200


def test_super_admin_toggle_endpoint(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    sa = make_user(db_session, role=SUPER_ADMIN, email=_e("sa"))
    assert login(client, sa.email).status_code == 200
    r = client.patch(f"/api/v1/tenants/{t.id}/active", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    db_session.expire_all()
    assert db_session.get(Tenant, t.id).is_active is False
    assert client.patch(f"/api/v1/tenants/{t.id}/active", json={"is_active": True}).json()["is_active"] is True


def test_tenant_admin_cannot_toggle(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=t, email=_e("ta"))
    assert login(client, ta.email).status_code == 200
    assert client.patch(f"/api/v1/tenants/{t.id}/active", json={"is_active": False}).status_code == 403


def test_disabled_customer_is_not_billed_and_mail_not_read(db_session, monkeypatch):
    from app.core import email_puller
    from app.core.finance_aggregation import summarize_tenant_bill

    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:5]}")
    db_session.add(ConnectedMailbox(provider="gmail", email=_e("mb"), host="imap.example.com",
                                    app_password_encrypted="x", tenant_id=t.id, paused=False))
    t.is_active = False
    db_session.commit()
    bill = summarize_tenant_bill(db_session, t, date.today().replace(day=1))
    assert bill.platform_fee == 0 and bill.total_amount == 0 and bill.is_active is False   # no usage, no fee
    monkeypatch.setattr("app.core.mail_crypto.decrypt_secret", lambda s: "pw")
    import inspect
    fn = next(f for name, f in inspect.getmembers(email_puller, inspect.isfunction)
              if "ConnectedMailbox.paused.is_(False)" in inspect.getsource(f))
    got = fn(db_session) if len(inspect.signature(fn).parameters) == 1 else fn(db_session, None)
    assert all(getattr(m, "tenant_id", None) != t.id and (not isinstance(m, dict) or m.get("tenant_id") != t.id) for m in got)
