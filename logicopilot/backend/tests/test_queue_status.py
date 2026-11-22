"""P3 queue visibility: every queue's waiting/running/oldest age, stale + no-worker warnings,
and the servers' heartbeats. Read-only."""
import json
import time
from types import SimpleNamespace

from app.core import queue_status as qs
from tests.test_mail_queue import FakeRedis


class R(FakeRedis):
    def __init__(self):
        super().__init__()
        self.kv = {}

    def lindex(self, k, i):
        lst = self.lists.get(k) or []
        return lst[i] if lst else None

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def get(self, k):
        return self.kv.get(k)

    def scan_iter(self, match=None, count=None):
        pre = match.rstrip("*")
        return [k for k in self.kv if k.startswith(pre)]


def test_snapshot_queues_oldest_and_warnings(monkeypatch):
    r = R()
    monkeypatch.setattr(qs, "_client", lambda: r)
    now = time.time()
    r.lpush("mail:queue", json.dumps({"queued_at": now - 30}))
    r.lpush("mail:queue", json.dumps({"queued_at": now - 5}))       # newer on the left
    r.lpush("extract:queue", json.dumps({"queued_at": now - 900}))  # 15 min old
    r.lpush("extract:processing", json.dumps({"queued_at": now}))
    r.set("proc:server.cargora-ai:worker", json.dumps(
        {"host": "server.cargora-ai", "role": "worker", "at": now - 3,
         "mail_workers": 5, "extract_workers": 6, "erp_workers": 0}))
    snap = qs.snapshot()
    q = {x["name"]: x for x in snap["queues"]}
    assert (q["mail"]["waiting"], q["mail"]["oldest_wait_seconds"]) == (2, 30)
    assert q["extract"]["running"] == 1 and q["extract"]["stale"] is True
    assert q["erp"]["waiting"] == 0 and q["erp"]["oldest_wait_seconds"] is None
    assert snap["servers"][0]["host"] == "server.cargora-ai"
    assert any("Extraction" in w and "15 min" in w for w in snap["warnings"])
    r.lpush("erp:queue", json.dumps({"queued_at": now}))
    assert any("ERP entry" in w and "no server" in w for w in qs.snapshot()["warnings"])


def test_no_redis_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(qs, "_client", lambda: None)
    assert qs.snapshot() == {"redis": False, "queues": [], "servers": [], "warnings": []}


def test_endpoint_super_admin_only(client, db_session, monkeypatch):
    from tests.conftest import login, make_tenant, make_user

    monkeypatch.setattr(qs, "_client", lambda: None)
    t = make_tenant(db_session)
    make_user(db_session, role="tenant_admin", tenant=t, email="ta-q@example.com")
    login(client, "ta-q@example.com")
    assert client.get("/api/v1/system-settings/queues").status_code == 403
    make_user(db_session, role="super_admin", email="sa-q@example.com")
    login(client, "sa-q@example.com")
    assert client.get("/api/v1/system-settings/queues").json()["redis"] is False
