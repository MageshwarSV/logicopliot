"""Live extraction progress: phases, counts and an estimate while a job is extracting, and
nothing at all once it is not (no stale bar after a failed run)."""
import time

from app.core import extraction_progress as xp
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def test_phases_counts_and_estimate():
    xp.clear("j1")
    xp.publish("j1", "ai", 0, 14)
    time.sleep(0.02)
    xp.publish("j1", "ai", 7, 14)
    f = xp.latest("j1")
    assert f["phase"] == "ai" and (f["done"], f["total"]) == (7, 14) and f["eta_seconds"] is not None
    started = f["started"]
    xp.publish("j1", "docs", 1, 10)   # a new phase keeps the run's start, restarts the phase clock
    f = xp.latest("j1")
    assert f["started"] == started and f["phase"] == "docs"
    xp.publish("j1", "finishing")
    assert xp.latest("j1")["eta_seconds"] is None
    xp.clear("j1")
    assert xp.latest("j1") is None


def _job(db, status):
    tenant = make_tenant(db)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db.add(group)
    db.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-XP", status=status)
    db.add(job)
    db.commit()
    return tenant, job


def test_endpoint_reports_only_while_extracting(client, db_session):
    tenant, job = _job(db_session, "extracting")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-xp@example.com")
    login(client, ta.email)
    url = f"/api/v1/jobs/{job.id}/extraction-progress"
    assert client.get(url).json() == {"active": False}          # nothing published yet
    xp.publish(job.id, "ai", 3, 14)
    body = client.get(url).json()
    assert body["active"] and body["phase"] == "ai" and body["done"] == 3 and body["total"] == 14
    job.status = "draft"                                         # the run failed / finished
    db_session.commit()
    assert client.get(url).json() == {"active": False}          # never a stale bar
    xp.clear(job.id)
