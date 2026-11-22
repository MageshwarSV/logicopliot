"""Step 1.2: a split WEB server hands Run / Re-run extraction to the WORKER server through Redis;
"all" (every server today) still starts it in-process exactly as before."""
import json
from types import SimpleNamespace

import pytest

from app.api.v1 import jobs as jobs_api
from app.core import extraction_dispatch as xd
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


class FakeRedis:
    def __init__(self):
        self.lists = {}

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def brpop(self, key, timeout=0):
        lst = self.lists.get(key) or []
        return (key, lst.pop()) if lst else None

    def llen(self, key):
        return len(self.lists.get(key) or [])

    def delete(self, key):
        self.lists.pop(key, None)


@pytest.fixture
def env(monkeypatch):
    """Role + Redis switchable per test; in-process starts recorded instead of run."""
    state = SimpleNamespace(role="all", redis=FakeRedis(), started=[])
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(process_role=state.role, redis_url="redis://x"))
    monkeypatch.setattr(xd, "_client", lambda: state.redis)
    monkeypatch.setattr("app.core.extraction_queue.submit_extraction",
                        lambda fn: state.started.append(fn))
    monkeypatch.setattr(xd, "_inflight", set())
    return state


def _queued(state):
    return [json.loads(m) for m in reversed(state.redis.lists.get(xd.QUEUE_KEY, []))]


def test_all_runs_in_process_as_before(env):
    env.role = "all"
    jobs_api._start_extraction_background("j1", is_rerun=True)
    assert len(env.started) == 1 and _queued(env) == []


def test_all_never_dedupes(env):
    """One process doing everything: unchanged, so two starts are two starts (as before)."""
    env.role = "all"
    jobs_api._start_extraction_background("j1")
    jobs_api._start_extraction_background("j1")
    assert len(env.started) == 2


def test_web_hands_off_fifo(env):
    env.role = "web"
    jobs_api._start_extraction_background("j1")
    jobs_api._start_extraction_background("j2", only_job_document_id="d9", is_rerun=True)
    assert env.started == []
    q = _queued(env)
    assert [m["job_id"] for m in q] == ["j1", "j2"]          # oldest first
    assert q[1]["doc"] == "d9" and q[1]["is_rerun"] is True


def test_web_without_redis_runs_here_instead_of_losing_the_job(env):
    env.role = "web"
    env.redis = None
    jobs_api._start_extraction_background("j1")
    assert len(env.started) == 1


def test_split_worker_reads_a_job_once(env):
    """Startup resume and a doorbell message can name the same job: read it once."""
    env.role = "worker"
    jobs_api._start_extraction_background("j1")
    jobs_api._start_extraction_background("j1")
    assert len(env.started) == 1


def _job(db, status):
    tenant = make_tenant(db)
    g = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db.add(g)
    db.commit()
    j = Job(tenant_id=tenant.id, group_id=g.id, reference="JOB-XD", status=status)
    db.add(j)
    db.commit()
    return j


@pytest.mark.parametrize("status,starts", [("extracting", 1), ("draft", 0), ("ready", 0)])
def test_worker_only_reads_jobs_still_extracting(env, db_session, session_factory, monkeypatch,
                                                 status, starts):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)
    env.role = "worker"
    job = _job(db_session, status)
    xd._handle(json.dumps({"job_id": job.id, "doc": None, "is_rerun": False}))
    assert len(env.started) == starts


def test_unreadable_message_is_skipped(env):
    env.role = "worker"
    xd._handle("not json")
    assert env.started == []


def test_clear_queue_before_resume(env):
    env.role = "web"
    jobs_api._start_extraction_background("j1")
    xd.clear_queue()
    assert _queued(env) == []
