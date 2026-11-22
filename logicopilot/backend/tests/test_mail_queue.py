"""P1 mail queue: the poller queues harvested mail, workers read it with the SAME processing,
nothing is queued twice, a restart puts interrupted mail back first, and with MAIL_QUEUE off
nothing changes."""
import json
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from app.core import email_puller as ep
from app.core import mail_queue as mq


class FakeRedis:
    def __init__(self):
        self.lists, self.hashes = {}, {}

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def brpoplpush(self, src, dst, timeout=0):
        items = self.lists.get(src) or []
        if not items:
            return None
        v = items.pop()
        self.lists.setdefault(dst, []).insert(0, v)
        return v

    def lrem(self, k, n, v):
        if v in self.lists.get(k, []):
            self.lists[k].remove(v)

    def llen(self, k):
        return len(self.lists.get(k, []))

    def eval(self, script, n, src, dst):
        items = self.lists.get(src) or []
        if not items:
            return None
        v = items.pop(0)
        self.lists.setdefault(dst, []).append(v)
        return v

    def hsetnx(self, h, k, v):
        d = self.hashes.setdefault(h, {})
        if k in d:
            return 0
        d[k] = v
        return 1

    def hget(self, h, k):
        return self.hashes.get(h, {}).get(k)

    def hset(self, h, k, v):
        self.hashes.setdefault(h, {})[k] = v

    def hdel(self, h, k):
        self.hashes.get(h, {}).pop(k, None)


def _raw(mid="<m1@test>", subject="AWB 123"):
    m = EmailMessage()
    m["Message-ID"], m["Subject"], m["From"] = mid, subject, "shipper@example.com"
    m.set_content("see attached")
    return m.as_bytes()


@pytest.fixture
def env(monkeypatch, tmp_path):
    r = FakeRedis()
    st = SimpleNamespace(mail_queue=True, process_role="worker", redis_url="redis://x",
                         uploads_dir=str(tmp_path), gmail_user="", vision_engine_model="m")
    monkeypatch.setattr("app.core.config.get_settings", lambda: st)
    monkeypatch.setattr(mq, "_client", lambda: r)
    return SimpleNamespace(r=r, st=st, tmp=tmp_path)


def _task(mid="<m1@test>", **extra):
    return {"uid": b"7", "raw": _raw(mid), "src": {"mailbox_id": "mb1", "tenant_id": "t1",
                                                     "group_id": "g1"}, **extra}


def test_off_by_default_and_only_where_mail_is_read(env):
    assert mq.enabled()
    env.st.mail_queue = False
    assert not mq.enabled()
    env.st.mail_queue, env.st.process_role = True, "web"
    assert not mq.enabled()


def test_enqueue_saves_eml_and_a_small_item(env):
    assert mq.enqueue(_task(), large=False)
    item = json.loads(env.r.lists[mq.QUEUE_KEY][0])
    assert item["mid"] == "<m1@test>" and item["uid"] == "7" and item["mailbox_id"] == "mb1"
    assert "raw" not in item and "password" not in json.dumps(item)
    assert (env.tmp / "_mailq" / item["eml"]).read_bytes() == _raw()


def test_same_mail_is_not_queued_twice_but_a_stale_marker_is_ignored(env):
    assert mq.enqueue(_task())
    assert not mq.enqueue(_task())                      # next minute's poll finds it again
    assert env.r.llen(mq.QUEUE_KEY) == 1
    env.r.hashes[mq.KEYS_HASH]["<m1@test>"] = "0"       # marker from a lost item, long ago
    assert mq.enqueue(_task())


def test_oldest_first_and_large_goes_to_its_own_queue(env):
    for m in ("<a@t>", "<b@t>"):
        mq.enqueue(_task(m))
    mq.enqueue(_task("<big@t>"), large=True)
    picked = [json.loads(env.r.brpoplpush(mq.QUEUE_KEY, mq.PROCESSING_KEY))["mid"] for _ in range(2)]
    assert picked == ["<a@t>", "<b@t>"]
    assert json.loads(env.r.lists[mq.LARGE_KEY][0])["mid"] == "<big@t>"


def test_restart_puts_interrupted_mail_back_first(env):
    for m in ("<a@t>", "<b@t>", "<c@t>"):
        mq.enqueue(_task(m))
    env.r.brpoplpush(mq.QUEUE_KEY, mq.PROCESSING_KEY)  # a and b were being read...
    env.r.brpoplpush(mq.QUEUE_KEY, mq.PROCESSING_KEY)
    mq.enqueue(_task("<d@t>"))                          # ...d arrived, then the process died
    assert mq.requeue_orphans() == 2
    order = []
    while (v := env.r.brpoplpush(mq.QUEUE_KEY, mq.PROCESSING_KEY)) is not None:
        order.append(json.loads(v)["mid"])
    assert order == ["<a@t>", "<b@t>", "<c@t>", "<d@t>"]


def test_worker_runs_the_same_processing_and_cleans_up(env, monkeypatch):
    mq.enqueue(_task(split_checked=True))
    item = json.loads(env.r.lists[mq.QUEUE_KEY][0])
    seen = {}
    monkeypatch.setattr(mq, "_source_for", lambda db, it: {
        "mailbox_id": "mb1", "email": "box@x", "password": "pw", "host": "imap.x",
        "tenant_id": "t1", "group_id": "g1"})
    monkeypatch.setattr(ep, "_mailbox_setup", lambda db, t, g: ({"ctx": True}, None))
    monkeypatch.setattr("app.core.system_settings.get_extraction_engine", lambda db: "eng")
    monkeypatch.setattr("app.core.custom_page_filter.get_active_custom_filter_texts", lambda db: [])
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: SimpleNamespace(close=lambda: None))

    def fake_process(db, uid, raw, ctx, mail_email, pw, host, engine, model, filters, reexamine, **kw):
        seen.update(uid=uid, raw=raw, ctx=ctx, email=mail_email, pw=pw, kw=kw)
        return {"job_id": "J1"}

    monkeypatch.setattr(ep, "_process_one_message", fake_process)
    assert mq.run_item(item) == {"job_id": "J1"}
    assert seen["uid"] == b"7" and seen["raw"] == _raw() and seen["pw"] == "pw"
    assert seen["kw"] == {"split_checked": True}
    assert not (env.tmp / "_mailq" / item["eml"]).exists()
    assert "<m1@test>" not in env.r.hashes.get(mq.KEYS_HASH, {})


class _Q:
    """Minimal db stand-in for _queue_harvested_mail: EmailSeen lookups + groups."""

    def __init__(self, verdicts, group=None):
        self.verdicts, self.group = verdicts, group

    def query(self, *_a):
        outer = self

        class Q:
            def filter(self, cond):
                self.mid = cond.right.value
                return self

            def first(self):
                v = outer.verdicts.get(self.mid)
                return (v,) if v is not None else None
        return Q()

    def get(self, _model, _id):
        return self.group

    def rollback(self):
        pass


def test_poller_queues_new_mail_skips_examined_keeps_split_parts(env):
    tasks = [_task("<new@t>"), _task("<done@t>"), _task("<held@t>"),
             _task("<part@t>", parent_mid="<p@t>")]
    db = _Q({"<done@t>": "matched", "<held@t>": ep.FAILED_VERDICT})
    keep, queued = ep._queue_harvested_mail(db, tasks, reexamine=False)
    assert queued == 2                                   # new + a held one retried
    assert [t.get("parent_mid") for t in keep] == ["<p@t>"]
    mids = sorted(json.loads(v)["mid"] for v in env.r.lists[mq.QUEUE_KEY])
    assert mids == ["<held@t>", "<new@t>"]


def test_a_mail_that_cannot_be_queued_is_read_in_the_cycle(env, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(mq, "enqueue", boom)
    keep, queued = ep._queue_harvested_mail(_Q({}), [_task("<x@t>")], reexamine=False)
    assert queued == 0 and len(keep) == 1
