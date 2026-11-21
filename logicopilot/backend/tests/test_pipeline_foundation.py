"""Phase 2a + 2b foundation: the job work folder, the frozen template, the stage queues, the
fan-in counters and the worker runner - and that all of it stays off unless switched on."""
import json

import pytest

from app.core.config import get_settings
from app.pipeline import queues, runner, snapshot, workdir


class FakeRedis:
    """Enough Redis for the pipeline: lists, a hash, sets, plain keys."""

    def __init__(self):
        self.lists, self.hashes, self.sets, self.keys = {}, {}, {}, {}

    def ping(self):
        return True

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)
        return len(self.lists[k])

    def brpoplpush(self, src, dst, timeout=0):
        items = self.lists.get(src) or []
        if not items:
            return None
        raw = items.pop()  # right end of src ...
        self.lists.setdefault(dst, []).insert(0, raw)  # ... to the left end of dst
        return raw

    def rpoplpush(self, src, dst):
        return self.brpoplpush(src, dst)

    def lrem(self, k, count, v):
        items = self.lists.get(k) or []
        if v in items:
            items.remove(v)
            return 1
        return 0

    def llen(self, k):
        return len(self.lists.get(k) or [])

    def hset(self, k, f, v):
        self.hashes.setdefault(k, {})[f] = str(v)

    def hget(self, k, f):
        return self.hashes.get(k, {}).get(f)

    def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    def sadd(self, k, v):
        s = self.sets.setdefault(k, set())
        if v in s:
            return 0
        s.add(v)
        return 1

    def scard(self, k):
        return len(self.sets.get(k, set()))

    def delete(self, *ks):
        for k in ks:
            self.lists.pop(k, None), self.hashes.pop(k, None), self.sets.pop(k, None), self.keys.pop(k, None)

    def set(self, k, v):
        self.keys[k] = v

    def get(self, k):
        return self.keys.get(k)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    import app.core.erp_redis_queue as erq

    monkeypatch.setattr(erq, "_redis", lambda: fake)
    return fake


@pytest.fixture
def on(monkeypatch, redis):
    monkeypatch.setattr(get_settings(), "pipeline_v2", True)
    return redis


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


# ---- 2a: work folder ---------------------------------------------------------------------

def test_work_files_are_versioned_and_atomic(uploads):
    p = workdir.page_ocr_path("job-1", 12)
    assert p == uploads / "jobs" / "job-1" / "work" / "pages" / "012.ocr.json"
    assert not workdir.done(p)
    workdir.write_json(p, {"text": "INVOICE"})
    assert workdir.read_json(p) == {"_v": workdir.SCHEMA_VERSION, "text": "INVOICE"}
    assert workdir.done(p)
    assert not list(p.parent.glob(".*.tmp"))  # no temp file left behind


def test_a_job_id_cannot_climb_out_of_uploads(uploads):
    for bad in ("../etc", "a/b", "", "x" * 65):
        with pytest.raises(ValueError):
            workdir.work_dir(bad)


def test_cost_log_survives_a_line_cut_off_by_a_crash(uploads):
    workdir.append_cost("job-1", {"stage": "ocr", "rupees": 0.12})
    with workdir.cost_log_path("job-1").open("a") as fh:
        fh.write('{"stage": "extr')  # half a line, as a crash would leave it
    workdir.append_cost("job-1", {"stage": "extract", "rupees": 0.30})
    assert [r["stage"] for r in workdir.read_costs("job-1")] == ["ocr", "extract"]


# ---- 2a: frozen template -----------------------------------------------------------------

def _template(db):
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup
    from tests.conftest import make_tenant

    tenant = make_tenant(db)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db.add(group)
    db.commit()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db.add(tdoc)
    db.commit()
    db.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     x=0.1, y=0.1, width=0.2, height=0.05))
    db.commit()
    return group, tdoc


def test_template_is_frozen_once_per_job(db_session, uploads):
    group, tdoc = _template(db_session)
    first = snapshot.freeze(db_session, "job-1", group.id)
    assert first["documents"][0]["name"] == "Invoice"
    assert first["documents"][0]["marks"][0]["label_name"] == "Invoice No"
    tdoc.name = "Commercial Invoice"  # an admin edits the template mid-job
    db_session.commit()
    again = snapshot.freeze(db_session, "job-1", group.id)
    assert again["version"] == first["version"]
    assert again["documents"][0]["name"] == "Invoice"  # the job keeps what it started with
    assert snapshot.build(db_session, group.id)["version"] != first["version"]  # a new job sees the edit


# ---- 2b: queues --------------------------------------------------------------------------

def test_oldest_item_comes_out_first(redis):
    for n in (1, 2, 3):
        queues.enqueue("ocr", {"page": n})
    assert [queues.reserve("ocr")[1]["page"] for _ in range(3)] == [1, 2, 3]


def test_reserved_work_is_handed_back_after_a_restart(redis):
    queues.enqueue("ocr", {"page": 1})
    queues.reserve("ocr")  # taken, then the process dies before ack
    assert queues.depth("ocr") == {"waiting": 0, "running": 1, "dead": 0}
    assert queues.requeue_orphans("ocr") == 1
    assert queues.depth("ocr") == {"waiting": 1, "running": 0, "dead": 0}
    assert queues.reserve("ocr")[1]["page"] == 1


def test_a_failing_item_is_retried_then_parked_on_the_dead_list(redis):
    queues.enqueue("extract", {"doc": 7})
    outcomes = []
    for _ in range(queues.MAX_ATTEMPTS):
        raw, item = queues.reserve("extract")
        outcomes.append(queues.retry("extract", raw, item, "OpenAI timeout"))
    assert outcomes == ["retry", "retry", "dead"]
    assert queues.depth("extract") == {"waiting": 0, "running": 0, "dead": 1}
    dead = json.loads(redis.lists[queues.dead_key("extract")][0])
    assert dead["doc"] == 7 and dead["attempts"] == 3 and "timeout" in dead["last_error"]


def test_the_next_stage_starts_exactly_once_after_the_last_page(redis):
    queues.set_total("job-1", "ocr", 3)
    calls = [queues.mark_done("job-1", "ocr", u) for u in (0, 1, 1, 2, 2)]
    triggers = [c for c in calls if c[0] == c[1] and c[2]]
    assert calls[2] == (2, 3, False)   # page 1 done twice (a retry) counts once
    assert len(triggers) == 1          # the move to classification fires once
    assert queues.progress("job-1") == {"ocr": {"done": 3, "total": 3}}


def test_unknown_stage_is_refused(redis):
    with pytest.raises(ValueError):
        queues.enqueue("typo", {})


# ---- 2b: runner --------------------------------------------------------------------------

def test_handler_success_failure_and_dead(redis, monkeypatch):
    seen = []
    monkeypatch.setitem(runner._handlers, "classify", lambda item: seen.append(item["job"]))
    queues.enqueue("classify", {"job": "j1"})
    assert runner.run_one("classify") == "done" and seen == ["j1"]
    assert runner.run_one("classify") is None  # empty

    def boom(item):
        raise RuntimeError("bad page")

    monkeypatch.setitem(runner._handlers, "classify", boom)
    queues.enqueue("classify", {"job": "j2"})
    assert [runner.run_one("classify") for _ in range(3)] == ["retry", "retry", "dead"]


def test_ping_batch_round_trip(on):
    batch = runner.send_pings(3)
    assert [runner.run_one("ping") for _ in range(3)] == ["done"] * 3
    assert queues.progress(batch) == {"ping": {"done": 3, "total": 3}}
    st = runner.status()
    assert st["enabled"] and st["redis"] and st["last_ping"]


# ---- off switch --------------------------------------------------------------------------

def test_off_by_default_nothing_is_queued(redis, monkeypatch):
    monkeypatch.setattr(get_settings(), "pipeline_v2", False)
    assert not runner.enabled()


def test_without_redis_nothing_is_queued(monkeypatch):
    import app.core.erp_redis_queue as erq

    monkeypatch.setattr(erq, "_redis", lambda: None)
    monkeypatch.setattr(get_settings(), "pipeline_v2", True)
    assert not runner.enabled()
    assert queues.enqueue("ocr", {"page": 1}) is False
    assert runner.send_pings(3) is None


def test_ping_endpoint_refuses_when_off(client, db_session, monkeypatch):
    from tests.conftest import login, make_user

    monkeypatch.setattr(get_settings(), "pipeline_v2", False)
    sa = make_user(db_session, role="super_admin", email="sa-p2@example.com")
    login(client, sa.email)
    assert client.post("/api/v1/system-settings/pipeline/ping", json={"count": 2}).status_code == 409
    assert client.get("/api/v1/system-settings/pipeline").json()["enabled"] is False
