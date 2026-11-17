"""Reports -> User Job Summary: per user, jobs that arrived in the period for one operation,
how many reached the ERP, and how many are pending (Total = Completed + Pending)."""
import io
import uuid
from datetime import datetime, timezone

import openpyxl

from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

URL = "/api/v1/jobs/user-summary"


def _setup(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    admin = make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    ashraf = make_user(db_session, role=OPERATOR, tenant=tenant, email=f"a-{uuid.uuid4().hex}@example.com", full_name="Ashraf")
    magesh = make_user(db_session, role=OPERATOR, tenant=tenant, email=f"m-{uuid.uuid4().hex}@example.com", full_name="Magesh")
    sea = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="approved", mode="Sea Import")
    air = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved", mode="Air Import")
    db_session.add_all([sea, air])
    db_session.commit()
    n = [0]

    def job(group, creator, created, done=False, routed=None):
        n[0] += 1
        db_session.add(Job(
            tenant_id=tenant.id, group_id=group.id, reference=f"JOB-{n[0]}",
            status="completed" if done else "extracted",
            created_by_id=creator.id if creator else None,
            assigned_operator_id=routed.id if routed else None,
            created_at=created,
            completed_at=created if done else None, ai_submitted_at=created if done else None,
        ))

    nov17 = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)   # 11:30 IST, 17 Nov
    job(sea, ashraf, nov17, done=True)
    job(sea, ashraf, nov17)
    job(sea, ashraf, nov17)
    job(sea, None, nov17, routed=magesh, done=True)                 # mail-routed to Magesh
    job(sea, None, nov17)                                          # nobody: Unassigned
    job(air, ashraf, nov17, done=True)                             # other operation
    job(sea, magesh, datetime(2026, 11, 16, 19, 0, tzinfo=timezone.utc))  # 00:30 IST 17 Nov
    job(sea, magesh, datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc))  # 23:30 IST 16 Nov
    db_session.commit()
    assert login(client, admin.email).status_code == 200
    return tenant


def test_per_user_counts_for_one_day_and_operation(client, db_session):
    _setup(client, db_session)
    body = client.get(URL, params={"mode": "Sea Import", "date_from": "2026-11-17", "date_to": "2026-11-17"}).json()
    assert body["modes"] == ["Air Import", "Sea Import"]
    rows = [(r["name"], r["total"], r["completed"], r["pending"]) for r in body["users"]]
    # Magesh: the mail-routed job plus the one created 00:30 IST on 17 Nov (still 16 Nov in UTC);
    # his 23:30 IST 16 Nov job is not today's.
    assert rows == [("Ashraf", 3, 1, 2), ("Magesh", 2, 1, 1), ("Unassigned", 1, 0, 1)]
    assert body["totals"] == {"total": 6, "completed": 2, "pending": 4}


def test_ist_day_boundaries_and_ranges(client, db_session):
    _setup(client, db_session)
    yday = client.get(URL, params={"mode": "Sea Import", "date_from": "2026-11-16", "date_to": "2026-11-16"}).json()
    assert yday["totals"] == {"total": 1, "completed": 0, "pending": 1}
    both = client.get(URL, params={"mode": "Sea Import", "date_from": "2026-11-16", "date_to": "2026-11-17"}).json()
    assert both["totals"]["total"] == 7


def test_air_and_all_operations(client, db_session):
    _setup(client, db_session)
    air = client.get(URL, params={"mode": "Air Import", "date_from": "2026-11-17", "date_to": "2026-11-17"}).json()
    assert [(r["name"], r["total"], r["completed"]) for r in air["users"]] == [("Ashraf", 1, 1)]
    everything = client.get(URL, params={"date_from": "2026-11-17", "date_to": "2026-11-17"}).json()
    assert everything["totals"]["total"] == 7


def test_bad_dates_rejected(client, db_session):
    _setup(client, db_session)
    assert client.get(URL, params={"date_from": "2026-11-17", "date_to": "2026-11-01"}).status_code == 422
    assert client.get(URL, params={"date_from": "17-11-2026", "date_to": "2026-11-17"}).status_code == 422


def test_export_has_rows_and_total(client, db_session):
    _setup(client, db_session)
    r = client.get(f"{URL}/export", params={"mode": "Sea Import", "date_from": "2026-11-17", "date_to": "2026-11-17"})
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[3]] == ["S.No", "User", "Total Jobs", "ERP Completed Jobs", "Pending Jobs"]
    assert [c.value for c in ws[ws.max_row]][1:] == ["Total", 6, 2, 4]


def test_operator_cannot_open_the_report(client, db_session):
    tenant = make_tenant(db_session, name=f"T-{uuid.uuid4().hex[:6]}")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email=f"ta-{uuid.uuid4().hex}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email=f"op-{uuid.uuid4().hex}@example.com")
    assert login(client, op.email).status_code == 200
    assert client.get(URL, params={"date_from": "2026-11-17", "date_to": "2026-11-17"}).status_code == 403
