"""P2 extraction queue: every extraction start goes through Redis when EXTRACT_QUEUE is on,
runs through the shared pool, is not queued twice, survives restarts, and off = unchanged."""
import json
from types import SimpleNamespace

import pytest

from app.api.v1 import jobs as jobs_api
from app.core import extract_queue as xq
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant
from tests.test_mail_queue import FakeRedis


@pytest.fixture
def env(monkeypatch):
    r = FakeRedis()
    st = SimpleNamespace(extract_queue=True, process_role="worker", redis_url="redis://x")
    monkeypatch.setattr("app.core.config.get_settings", lambda: st)
    monkeypatch.setattr(xq, "_client", lambda: r)
    monkeypatch.setattr("app.core.extraction_dispatch._inflight", set())
    started = []
    monkeypatch.setattr("app.core.extraction_queue.submit_extraction", lambda fn: started.append(fn))
    return SimpleNamespace(r=r, st=st, started=started)


def _ids(r, key=xq.QUEUE_KEY):
    return [json.loads(v)["job_id"] for v in reversed(r.lists.get(key, []))]


def test_start_goes_on_the_queue_when_on(env):
    jobs_api._start_extraction_background("j1")
    jobs_api._start_extraction_background("j2", only_job_document_id="d9", is_rerun=True)
    assert env.started == [] and _ids(env.r) == ["j1", "j2"]
    item = json.loads(env.r.lists[xq.QUEUE_KEY][0])
    assert item["doc"] == "d9" and item["is_rerun"] is True


def test_off_runs_in_process_as_before(env):
    env.st.extract_queue = False
    env.st.process_role = "all"
    jobs_api._start_extraction_background("j1")
    assert len(env.started) == 1 and _ids(env.r) == []


def test_same_job_is_not_queued_twice(env):
    jobs_api._start_extraction_background("j1")
    jobs_api._start_extraction_background("j1")
    assert _ids(env.r) == ["j1"]


def test_queue_failure_falls_back_instead_of_losing_the_run(env, monkeypatch):
    env.st.process_role = "all"

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(xq, "enqueue", boom)
    jobs_api._start_extraction_background("j1")
    assert len(env.started) == 1


def test_restart_puts_interrupted_runs_back_first(env):
    for j in ("a", "b", "c"):
        xq.enqueue(j)
    env.r.brpoplpush(xq.QUEUE_KEY, xq.PROCESSING_KEY)      # a, b running, then the process died
    env.r.brpoplpush(xq.QUEUE_KEY, xq.PROCESSING_KEY)
    xq.enqueue("d")
    assert xq.requeue_orphans() == 2
    order = []
    while (v := env.r.brpoplpush(xq.QUEUE_KEY, xq.PROCESSING_KEY)) is not None:
        order.append(json.loads(v)["job_id"])
    assert order == ["a", "b", "c", "d"]


def _job(db, status):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Air Import", status="approved", mode="Air Import")
    db.add(g)
    db.commit()
    j = Job(tenant_id=t.id, group_id=g.id, reference="JOB-XQ", status=status)
    db.add(j)
    db.commit()
    return j


@pytest.mark.parametrize("status,runs", [("extracting", 1), ("extracted", 0), ("draft", 0)])
def test_worker_runs_only_jobs_still_extracting_through_the_shared_pool(
        env, db_session, session_factory, monkeypatch, status, runs):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)
    ran, pooled = [], []
    monkeypatch.setattr(jobs_api, "_run_extraction_job",
                        lambda jid, doc=None, is_rerun=False: ran.append((jid, doc, is_rerun)))
    monkeypatch.setattr("app.core.extraction_queue.run_queued", lambda fn: (pooled.append(1), fn()))
    job = _job(db_session, status)
    xq.enqueue(job.id, "d1", True)
    xq.run_item(json.loads(env.r.lists[xq.QUEUE_KEY][0]))
    assert len(ran) == runs and len(pooled) == runs
    if runs:
        assert ran[0] == (job.id, "d1", True)
    assert job.id not in env.r.hashes.get(xq.KEYS_HASH, {})   # marker always cleared


def test_workers_only_on_the_extracting_process(env, monkeypatch):
    calls = []
    monkeypatch.setattr(xq, "requeue_orphans", lambda: calls.append("requeue"))
    monkeypatch.setattr(xq.threading, "Thread", lambda **k: SimpleNamespace(
        start=lambda: calls.append(k["name"]), is_alive=lambda: True, name=k["name"]))
    monkeypatch.setattr(xq, "_workers", [])
    env.st.process_role = "web"
    xq.start()
    assert calls == []
    env.st.process_role = "worker"
    xq.start()
    assert calls[0] == "requeue" and "extract-q1" in calls
