"""Production Support (S1): the role, its overview / issues, read access, and Retry limits."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.job import Job, JobDocument
from app.models.job_event import JobEvent
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, SUPPORT, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _email(p):
    return f"{p}-{uuid.uuid4().hex[:8]}@example.com"


def _world(db):
    now = datetime.now(timezone.utc)
    out = {}
    for name in ("4S Logistics", "KCL"):
        t = make_tenant(db, name=f"{name}-{uuid.uuid4().hex[:4]}")
        make_user(db, role=TENANT_ADMIN, tenant=t, email=_email("ta"))
        g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved", mode="Sea Import")
        db.add(g)
        db.flush()
        td = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Invoice", doc_type="INVOICE", is_required=False)
        db.add(td)
        db.flush()
        out[name] = (t, g, td)
    t, g, td = out["4S Logistics"]

    def job(ref, tenant, group, **kw):
        j = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, created_at=kw.pop("created_at", now), **kw)
        db.add(j)
        db.flush()
        return j

    job("JOB-OK", t, g, status="completed", ai_submitted_at=now)
    failed = job("JOB-ERR", t, g, status="extracted", gk2_status="failed", gk2_skipped=True,
                 erp_status="failed", erp_failed_at=now - timedelta(minutes=30),
                 erp_help={"items": [{"display": "Country of Origin Code", "needs": "A 2-letter country code."}]})
    unfinished = job("JOB-DRAFT", t, g, status="draft", created_at=now - timedelta(minutes=30))
    db.add(JobDocument(tenant_id=t.id, job_id=unfinished.id, template_document_id=td.id,
                       file_path="uploads/x.pdf", page_count=3))
    slow = job("JOB-SLOW", t, g, status="extracting")
    t2, g2, _ = out["KCL"]
    job("JOB-KCL", t2, g2, status="extracted")
    db.commit()
    # Every status change writes its own event; extraction "started" 9 minutes ago.
    db.query(JobEvent).filter(JobEvent.job_id == slow.id).update(
        {"created_at": now - timedelta(minutes=9)}, synchronize_session=False)
    db.commit()
    return failed, unfinished


def _support(client, db):
    u = make_user(db, role=SUPPORT, email=_email("sup"), full_name="Desk One")
    assert login(client, u.email).status_code == 200
    return u


def test_super_admin_creates_a_support_user(client, db_session):
    sa = make_user(db_session, role=SUPER_ADMIN, email=_email("sa"))
    assert login(client, sa.email).status_code == 200
    r = client.post("/api/v1/users", json={"email": _email("desk"), "password": "Passw0rd!x",
                                           "full_name": "Desk", "role": "support"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["role"] == "support" and r.json().get("tenant_id") is None


def test_overview_counts_every_customer(client, db_session):
    _world(db_session)
    _support(client, db_session)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    body = client.get("/api/v1/support/overview", params={"date_from": today, "date_to": today}).json()
    tiles = body["tiles"]
    assert tiles["total_jobs"] >= 4 and tiles["failed"] >= 1 and tiles["submitted"] >= 1
    stages = {p["key"]: p["count"] for p in body["pipeline"]}
    assert stages["failed"] >= 1 and stages["ai_processing"] >= 1 and stages["ready_for_review"] >= 1
    attention = {a["key"]: a["count"] for a in body["attention"]}
    assert attention["erp_failed"] >= 1 and attention["extraction_failed"] >= 1 and attention["extraction_slow"] >= 1


def test_issues_say_what_and_who_may_retry(client, db_session):
    _world(db_session)
    _support(client, db_session)
    body = client.get("/api/v1/support/issues").json()
    by_ref = {i["reference"]: i for i in body["items"]}
    err = by_ref["JOB-ERR"]
    assert err["tab"] == "erp" and err["can_retry_erp"] is True
    assert err["reason"] == "ERP did not accept Country of Origin Code: A 2-letter country code."
    assert err["client"].startswith("4S Logistics")
    assert by_ref["JOB-DRAFT"]["kind"] == "extraction_failed" and by_ref["JOB-DRAFT"]["can_retry_extraction"]
    assert by_ref["JOB-SLOW"]["kind"] == "extraction_slow" and not by_ref["JOB-SLOW"]["can_retry_extraction"]
    assert "JOB-OK" not in by_ref and "JOB-KCL" not in by_ref
    erp_only = client.get("/api/v1/support/issues", params={"tab": "erp"}).json()
    assert {i["reference"] for i in erp_only["items"]} == {"JOB-ERR"}


def test_support_reads_jobs_but_cannot_manage_or_edit(client, db_session):
    failed, _ = _world(db_session)
    _support(client, db_session)
    assert client.get(f"/api/v1/jobs/{failed.id}").status_code == 200
    # Super Admin pages stay closed (templates, queues, users)
    assert client.get("/api/v1/system-settings/queues").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
    assert client.post("/api/v1/users", json={"email": _email("x"), "password": "Passw0rd!x",
                                              "full_name": "X", "role": "support"}).status_code == 403
    assert client.post(f"/api/v1/jobs/{failed.id}/rerun").status_code == 403


def test_retry_only_on_failed_jobs(client, db_session):
    failed, unfinished = _world(db_session)
    _support(client, db_session)
    # ERP retry on a job that has not failed: refused
    failed.gk2_status = "pending"
    db_session.commit()
    r = client.post(f"/api/v1/jobs/{failed.id}/gk2/approve")
    assert r.status_code == 403
    # extraction retry on a job already extracted: refused (would replace the operator's work)
    r = client.post(f"/api/v1/jobs/{failed.id}/extract")
    assert r.status_code == 403 and "already been read" in r.json()["detail"]


def test_operator_cannot_open_support(client, db_session):
    t = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:4]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=t, email=_email("ta"))
    assert login(client, make_user(db_session, role="operator", tenant=t, email=_email("op")).email).status_code == 200
    assert client.get("/api/v1/support/issues").status_code == 403
