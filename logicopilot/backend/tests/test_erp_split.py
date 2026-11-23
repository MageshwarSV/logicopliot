"""Step 1.3: on a split setup ERP entries run only on the ERP server.

- Final Approve pressed on the web server puts the job on Redis but must NOT start ERP worker
  threads there (that would make the web server drive Chrome).
- The old in-request Submit Entry (/complete) refuses on a non-ERP server.
- "all" (every server today) is unchanged.
"""
import json
from types import SimpleNamespace

import pytest

from app.core import erp_redis_queue as q


class FakeRedis:
    def __init__(self):
        self.items = []

    def lpush(self, key, value):
        self.items.insert(0, (key, value))


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(role="all", redis=FakeRedis(), workers=[], inproc=[])
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(process_role=state.role, redis_url="redis://x"))
    monkeypatch.setattr(q, "_redis", lambda: state.redis)
    monkeypatch.setattr(q, "_ensure_workers", lambda n: state.workers.append(n))
    monkeypatch.setattr("app.core.erp_queue.submit_erp_run",
                        lambda n, fn: state.inproc.append(n))
    return state


@pytest.mark.parametrize("role,starts_workers", [("all", True), ("erp", True),
                                                  ("web", False), ("worker", False)])
def test_enqueue_starts_workers_only_where_erp_runs(env, role, starts_workers):
    env.role = role
    assert q.enqueue("j1", is_excel_entry=False, desired_workers=3) == "redis"
    assert json.loads(env.redis.items[0][1])["job_id"] == "j1"
    assert bool(env.workers) is starts_workers


def test_no_redis_still_runs_the_job(env):
    """Never lose an approved entry: without Redis it runs in-process (logged on a split)."""
    env.role = "web"
    env.redis = None
    assert q.enqueue("j1", is_excel_entry=False, desired_workers=2) == "memory"
    assert env.inproc == [2]


def test_complete_refused_on_a_web_server(client, db_session, monkeypatch):
    from app.models.job import Job
    from app.models.template_group import TemplateGroup
    from tests.conftest import login, make_tenant, make_user

    tenant = make_tenant(db_session)
    g = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db_session.add(g)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=g.id, reference="JOB-SPL", status="extracted")
    db_session.add(job)
    db_session.commit()
    ta = make_user(db_session, role="super_admin", email="sa-split@example.com")
    login(client, ta.email)

    monkeypatch.setattr("app.core.process_role.role", lambda: "web")
    r = client.post(f"/api/v1/jobs/{job.id}/complete", headers={"X-Requested-With": "x"})
    assert r.status_code == 409 and "Final Submit" in r.json()["detail"]
    db_session.refresh(job)
    assert job.status == "extracted"            # nothing touched
