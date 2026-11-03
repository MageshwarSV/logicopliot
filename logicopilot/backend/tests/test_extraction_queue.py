"""Extraction must never again take the whole connection pool.

Every extraction thread holds a database connection for its entire run - the queries are
quick, but the session keeps its transaction open across every OpenAI call, so the connection
sits `idle in transaction` for minutes. Unbounded, that reached 28 held connections against a
pool of 40 on production and the site stopped answering: page loads could not get a
connection, and get_current_user failing to get one looked to operators like being logged out.

So the thing worth testing is not that extraction runs - it is that no more than N of them
ever run AT ONCE, however many are thrown at it.
"""
import threading
import time

import pytest

from app.core import extraction_queue
from app.core.extraction_queue import DEFAULT_EXTRACTION_WORKERS, _worker_count


@pytest.fixture(autouse=True)
def _fresh_pool():
    """The pool is a module-level singleton, sized once on first use. Each test gets its own,
    or the first test's worker count would silently decide every later test's."""
    extraction_queue._pool = None
    yield
    pool, extraction_queue._pool = extraction_queue._pool, None
    if pool is not None:
        pool.shutdown(wait=True)


def _run_and_measure(submitted: int) -> int:
    """Submit `submitted` tasks that each hold their slot briefly, and report the highest
    number ever running at the same moment."""
    live = 0
    peak = 0
    lock = threading.Lock()
    done = threading.Event()
    finished = []

    def task() -> None:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        # Long enough that tasks genuinely overlap. Without this every task could finish
        # before the next is submitted, and the test would pass on a pool of any size.
        time.sleep(0.05)
        with lock:
            live -= 1
            finished.append(1)
            if len(finished) == submitted:
                done.set()

    for _ in range(submitted):
        extraction_queue.submit_extraction(task)

    assert done.wait(timeout=30), "tasks did not all finish"
    return peak


def test_no_more_than_the_cap_run_at_once():
    # Twenty jobs arriving together is an ordinary morning's email batch, and is roughly what
    # production was doing when it ran out of connections.
    peak = _run_and_measure(submitted=20)
    assert peak <= DEFAULT_EXTRACTION_WORKERS, (
        f"{peak} extractions ran at once; the cap is {DEFAULT_EXTRACTION_WORKERS}"
    )


def test_every_queued_job_still_runs():
    # The cap must DELAY work, never drop it. A job silently skipped would leave an operator
    # staring at "AI - Processing" forever with nothing running behind it.
    ran = []
    lock = threading.Lock()
    done = threading.Event()

    def task() -> None:
        with lock:
            ran.append(1)
            if len(ran) == 25:
                done.set()

    for _ in range(25):
        extraction_queue.submit_extraction(task)

    assert done.wait(timeout=30)
    assert len(ran) == 25


def test_the_cap_actually_binds():
    # Guards against a cap so high it never engages - which is the bug this replaces, just
    # with a bigger number. Submitting well over the cap MUST reach it.
    peak = _run_and_measure(submitted=DEFAULT_EXTRACTION_WORKERS * 3)
    assert peak > 1, "nothing ran concurrently at all - the pool is not doing its job"
    assert peak <= DEFAULT_EXTRACTION_WORKERS


def test_the_cap_leaves_the_website_most_of_the_pool():
    # The whole point. The pool is 20 + 20 overflow (see db/session.py); extraction may take
    # a minority of it so an operator loading a page can always get one.
    assert DEFAULT_EXTRACTION_WORKERS <= 10


@pytest.mark.parametrize(
    "env,expected",
    [
        (None, DEFAULT_EXTRACTION_WORKERS),
        ("", DEFAULT_EXTRACTION_WORKERS),
        ("   ", DEFAULT_EXTRACTION_WORKERS),
        ("10", 10),
        # Clamped at both ends: 0 would stop extraction happening at all, and a number near
        # the pool size puts us straight back into the outage this exists to prevent.
        ("0", 1),
        ("-4", 1),
        ("500", 20),
        ("not-a-number", DEFAULT_EXTRACTION_WORKERS),
    ],
)
def test_the_worker_count_can_be_tuned_on_the_box_but_not_broken(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("EXTRACTION_WORKERS", raising=False)
    else:
        monkeypatch.setenv("EXTRACTION_WORKERS", env)
    assert _worker_count() == expected


def test_extraction_is_queued_rather_than_spawned_outright():
    # The regression guard with teeth: _start_extraction_background must go through the pool.
    # Reverting it to threading.Thread(...) would restore the outage while every test above
    # still passed, because they only ever exercise the queue directly.
    import inspect

    from app.api.v1.jobs import _start_extraction_background

    source = inspect.getsource(_start_extraction_background)
    assert "submit_extraction(_go)" in source
    assert "threading.Thread" not in source
