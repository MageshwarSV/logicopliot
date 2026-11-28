"""Support mobile app API: Bearer login, home, issues with known fix, job detail, notes, devices, push."""
import uuid
from unittest import mock

from app.models.support_app import SupportDevice
from app.models.user import OPERATOR, SUPPORT, TENANT_ADMIN
from tests.conftest import DEFAULT_PASSWORD, make_tenant, make_user
from tests.test_support import _email, _world


def _bearer(client, db, role=SUPPORT, **kw):
    u = make_user(db, role=role, email=_email("sup"), full_name="Desk One", **kw)
    r = client.post("/api/v1/auth/login", json={"email": u.email, "password": DEFAULT_PASSWORD, "token_response": True})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return u, {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}


def test_phone_login_reads_home_with_a_bearer_token(client, db_session):
    _world(db_session)
    _u, h = _bearer(client, db_session)
    body = client.get("/api/v1/support/app/home", headers=h).json()
    assert body["health"]["status"] in ("ok", "warning", "down", "unknown")
    assert body["today"]["failed"] >= 1 and body["open_problems"] >= 1


def test_other_roles_cannot_use_the_app_api(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:4]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=t, email=_email("ta"))
    _u, h = _bearer(client, db_session, role=OPERATOR, tenant=t)
    assert client.get("/api/v1/support/app/home", headers=h).status_code == 403
    assert client.get("/api/v1/support/app/issues", headers=h).status_code == 403


def test_known_fix_written_once_shows_on_the_failed_job(client, db_session):
    failed, _ = _world(db_session)
    _u, h = _bearer(client, db_session)
    items = client.get("/api/v1/support/app/issues", headers=h).json()["items"]
    row = next(i for i in items if i["job_id"] == failed.id)
    assert row["issue_key"] == "erp_field:country of origin code"
    assert row["issue_title"] == "ERP refused Country of Origin Code" and row["known_fix"] is None

    r = client.put(f"/api/v1/support/app/known-issues/{row['issue_key']}", headers=h,
                   json={"title": "", "fix": "Pick the country from the dropdown, then Retry."})
    assert r.status_code == 200, r.text
    row = next(i for i in client.get("/api/v1/support/app/issues", headers=h).json()["items"]
               if i["job_id"] == failed.id)
    assert row["known_fix"].startswith("Pick the country")
    groups = client.get("/api/v1/support/app/known-issues", headers=h).json()["items"]
    g = next(g for g in groups if g["key"] == row["issue_key"])
    assert g["count"] >= 1 and g["jobs"][0]["reference"] == "JOB-ERR"


def test_job_detail_and_a_note_with_status(client, db_session):
    failed, _ = _world(db_session)
    _u, h = _bearer(client, db_session)
    d = client.get(f"/api/v1/support/app/jobs/{failed.id}", headers=h).json()
    assert d["reference"] == "JOB-ERR" and d["problem"] == "erp_failed" and d["can_retry_erp"] is True
    assert d["corrections"][0]["field"] == "Country of Origin Code"
    assert any(s["label"] == "ERP failed" for s in d["timeline"])

    bad = client.post(f"/api/v1/support/app/jobs/{failed.id}/notes", headers=h, json={"outcome": "maybe"})
    assert bad.status_code == 422
    r = client.post(f"/api/v1/support/app/jobs/{failed.id}/notes", headers=h,
                    json={"text": "Called KCL, they will fix the invoice", "outcome": "customer_informed"})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/v1/support/app/jobs/{failed.id}", headers=h).json()
    assert d["notes"][0]["outcome"] == "customer_informed" and d["notes"][0]["by"] == "Desk One"
    row = next(i for i in client.get("/api/v1/support/app/issues", headers=h).json()["items"]
               if i["job_id"] == failed.id)
    assert row["last_note"]["outcome_label"] == "Customer informed"


def test_phone_registers_and_gets_push_for_an_alert(client, db_session):
    _u, h = _bearer(client, db_session)
    assert client.post("/api/v1/support/app/devices", headers=h, json={"token": "junk"}).status_code == 422
    tok = "ExponentPushToken[abc123]"
    assert client.post("/api/v1/support/app/devices", headers=h, json={"token": tok, "platform": "android"}).status_code == 200
    assert db_session.query(SupportDevice).filter_by(push_token=tok).count() == 1

    from app.core import support_app

    with mock.patch.object(support_app, "_post", return_value=[{"status": "ok"}]) as post:
        assert support_app.send_push(db_session, "Job failed", "1 new failed job(s): JOB-ERR", {"job_id": "x"}) >= 1
    msgs = post.call_args[0][0]
    assert any(m["to"] == tok and m["title"] == "Job failed" for m in msgs)

    with mock.patch.object(support_app, "_post", return_value=[{"status": "error", "details": {"error": "DeviceNotRegistered"}}]):
        support_app.send_push(db_session, "t", "b")
    db_session.expire_all()
    assert db_session.query(SupportDevice).filter_by(push_token=tok).count() == 0
