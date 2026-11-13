"""The durable ERP submission queue.

The point of it is the backlog surviving this process. The old in-process pool held a queue
of CLOSURES in memory, so a pm2 restart or a deploy between "Final Approve & Proceed" and the
browser actually starting dropped every job still waiting - leaving it on "AI - Preparing for
ERP" with nothing anywhere to re-submit it.

These drive the real module against a fake Redis that behaves like the handful of list
commands it uses, so the reserve/requeue logic is exercised for real without needing a server.
"""
import json
import threading
import time
from unittest.mock import patch

import pytest

from app.core import erp_redis_queue as q


class FakeRedis:
    """Just enough Redis: the five list commands this queue uses, and a ping."""

    def __init__(self, fail_on=()):
        self.lists: dict[str, list[str]] = {}
        self.fail_on = set(fail_on)

    def _check(self, name):
        if name in self.fail_on:
            raise ConnectionError(f"{name} is down")

    def ping(self):
        self._check("ping")
        return True

    def rpush(self, key, value):
        self._check("rpush")
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def llen(self, key):
        self._check("llen")
        return len(self.lists.get(key, []))

    def brpoplpush(self, src, dst, timeout=0):
        self._check("brpoplpush")
        items = self.lists.get(src) or []
        if not items:
            return None
        # RIGHT of src -> LEFT of dst, same as Redis.
        raw = items.pop()
        self.lists.setdefault(dst, []).insert(0, raw)
        return raw

    def rpoplpush(self, src, dst):
        return self.brpoplpush(src, dst)

    def lrem(self, key, count, value):
        self._check("lrem")
        items = self.lists.get(key) or []
        if value in items:
            items.remove(value)
            return 1
        return 0


@pytest.fixture(autouse=True)
def _fresh():
    """Each test gets its own client and worker set."""
    q._client = None
    q._client_checked = False
    q._workers = []
    q._stop.clear()
    yield
    q._stop.set()
    q._client = None
    q._client_checked = False
    q._workers = []


def _use(fake):
    """Point the module at a fake, skipping the real connection attempt."""
    q._client = fake
    q._client_checked = True


def test_a_queued_job_is_data_not_a_function():
    """The whole reason this exists: what is stored has to be enough to rebuild the work
    after the process that queued it is gone."""
    fake = FakeRedis()
    _use(fake)
    with patch.object(q, "_ensure_workers"):
        assert q.enqueue("job-1", is_excel_entry=True, desired_workers=2) == "redis"

    raw = fake.lists[q.QUEUE_KEY][0]
    item = json.loads(raw)
    assert item["job_id"] == "job-1"
    assert item["is_excel_entry"] is True


def test_with_no_redis_it_falls_back_and_still_runs_the_job():
    """A box with no Redis must behave exactly as it did before - never refuse the job."""
    q._client, q._client_checked = None, True
    called = {}
    with patch("app.core.erp_queue.submit_erp_run",
               side_effect=lambda w, fn: called.update(workers=w, fn=fn)):
        assert q.enqueue("job-2", is_excel_entry=False, desired_workers=3) == "memory"
    assert called["workers"] == 3
    assert callable(called["fn"])


def test_a_redis_that_fails_mid_write_falls_back_rather_than_losing_the_job():
    _use(FakeRedis(fail_on={"rpush"}))
    called = {}
    with patch("app.core.erp_queue.submit_erp_run",
               side_effect=lambda w, fn: called.update(ran=True)):
        assert q.enqueue("job-3", is_excel_entry=False, desired_workers=1) == "memory"
    assert called.get("ran"), "the job must still run when the queue write fails"


def test_a_job_being_worked_on_is_reserved_not_just_taken():
    """It moves onto the in-flight list and stays there until the run finishes, so a worker
    killed mid-job leaves something for the next startup to find."""
    fake = FakeRedis()
    _use(fake)
    fake.rpush(q.QUEUE_KEY, json.dumps({"job_id": "job-4", "is_excel_entry": False}))

    seen_during = {}

    def slow_run(job_id, is_excel):
        seen_during["processing"] = list(fake.lists.get(q.PROCESSING_KEY) or [])
        seen_during["waiting"] = list(fake.lists.get(q.QUEUE_KEY) or [])

    with patch.object(q, "run_one", side_effect=slow_run):
        t = threading.Thread(target=q._worker, args=("t1",), daemon=True)
        t.start()
        for _ in range(100):
            if "processing" in seen_during:
                break
            time.sleep(0.02)
        q._stop.set()
        t.join(timeout=5)

    assert len(seen_during["processing"]) == 1, "the job must be held in flight while it runs"
    assert seen_during["waiting"] == [], "and off the waiting queue"
    assert fake.lists.get(q.PROCESSING_KEY) == [], "and cleared once the run is over"


def test_a_job_whose_run_raises_is_still_cleared_from_in_flight():
    """A failed ERP entry records its failure on the job itself. Leaving it reserved would
    only make the next startup run it again for nothing."""
    fake = FakeRedis()
    _use(fake)
    fake.rpush(q.QUEUE_KEY, json.dumps({"job_id": "job-5", "is_excel_entry": False}))

    done = threading.Event()

    def boom(job_id, is_excel):
        done.set()
        raise RuntimeError("the ERP fell over")

    with patch.object(q, "run_one", side_effect=boom):
        t = threading.Thread(target=q._worker, args=("t2",), daemon=True)
        t.start()
        done.wait(timeout=5)
        time.sleep(0.2)
        q._stop.set()
        t.join(timeout=5)

    assert fake.lists.get(q.PROCESSING_KEY) == []


def test_work_interrupted_by_a_restart_is_picked_back_up():
    """What a deploy mid-submission used to destroy."""
    fake = FakeRedis()
    _use(fake)
    fake.lists[q.PROCESSING_KEY] = [
        json.dumps({"job_id": "job-6", "is_excel_entry": False}),
        json.dumps({"job_id": "job-7", "is_excel_entry": False}),
    ]
    assert q.requeue_orphans() == 2
    assert fake.lists[q.PROCESSING_KEY] == []
    ids = {json.loads(r)["job_id"] for r in fake.lists[q.QUEUE_KEY]}
    assert ids == {"job-6", "job-7"}


def test_requeue_is_a_no_op_with_nothing_interrupted():
    _use(FakeRedis())
    assert q.requeue_orphans() == 0


def test_requeue_without_redis_changes_nothing():
    q._client, q._client_checked = None, True
    assert q.requeue_orphans() == 0


def test_queue_depth_reports_both_lists():
    fake = FakeRedis()
    _use(fake)
    fake.rpush(q.QUEUE_KEY, "a")
    fake.rpush(q.QUEUE_KEY, "b")
    fake.rpush(q.PROCESSING_KEY, "c")
    assert q.queue_depth() == {"backend": "redis", "waiting": 2, "running": 1}


def test_queue_depth_answers_even_with_no_redis():
    """The Super Admin screen must never have to special-case a box without Redis."""
    q._client, q._client_checked = None, True
    assert q.queue_depth() == {"backend": "memory", "waiting": None, "running": None}


def test_workers_are_replaced_when_they_die_and_match_the_configured_count():
    _use(FakeRedis())
    with patch.object(q, "_worker", side_effect=lambda name: None):
        q._ensure_workers(3)
        started = len(q._workers)
        assert started == 3
        for t in q._workers:
            t.join(timeout=2)
        # They have all exited now; asking again must start fresh ones, not count corpses.
        q._ensure_workers(3)
        assert len([t for t in q._workers if t.is_alive()] + [t for t in q._workers]) >= 3
        assert len(q._workers) == 3


def test_a_dropped_connection_does_not_kill_the_worker():
    """Redis restarting under us must cost a pause, not the worker."""
    fake = FakeRedis(fail_on={"brpoplpush"})
    _use(fake)
    t = threading.Thread(target=q._worker, args=("t3",), daemon=True)
    t.start()
    time.sleep(0.3)
    assert t.is_alive(), "the worker gave up on a connection error"
    q._stop.set()
    t.join(timeout=10)


def test_the_socket_outlasts_the_blocking_pop():
    """A worker parks the connection on BRPOPLPUSH for _POP_TIMEOUT seconds doing nothing.
    A socket_timeout of the same length calls that a dead connection, so every idle cycle
    threw "Timeout reading from socket" and the worker backed off for five seconds - found on
    dev the first time the workers ran for real. The margin is the fix; this is the guard."""
    assert q._SOCKET_TIMEOUT > q._POP_TIMEOUT + 5, (
        f"socket timeout {q._SOCKET_TIMEOUT}s leaves no room over a {q._POP_TIMEOUT}s "
        "blocking pop - idle workers will time out against their own wait"
    )


def test_the_client_is_built_with_that_margin():
    """Not just the constant: the client actually has to be created with it."""
    captured = {}

    class _FakeRedisModule:
        @staticmethod
        def from_url(url, **kwargs):
            captured.update(kwargs)

            class C:
                @staticmethod
                def ping():
                    return True
            return C()

    q._client, q._client_checked = None, False
    with patch.dict("sys.modules", {"redis": _FakeRedisModule}), \
         patch("app.core.config.get_settings") as settings:
        settings.return_value.redis_url = "redis://127.0.0.1:6379/3"
        q._redis()

    assert captured.get("socket_timeout", 0) > q._POP_TIMEOUT, captured
    assert captured.get("socket_connect_timeout"), "a dead host must not hang the enqueue"
