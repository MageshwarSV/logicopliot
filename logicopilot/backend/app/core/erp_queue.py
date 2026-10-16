"""A shared, size-limited worker pool for real ERP submissions (browser automation), so at
most N run at once instead of the old behavior - a brand-new, unbounded `threading.Thread`
per submission, with nothing stopping two jobs approved moments apart from running two
simultaneous ERP logins against the same account.

Mirrors email_puller.py's own ThreadPoolExecutor(max_workers=...) pattern, with one real
difference: email pulls happen in known batches (one ThreadPoolExecutor built fresh per pull
cycle, for that cycle's own list of mailboxes), while ERP submissions arrive one at a time,
triggered by whichever request happens to approve a job next - there is no "batch" moment to
build a fresh pool in. So this pool is long-lived for the app's whole process lifetime
instead, and simply resized in place whenever the configured worker count no longer matches
the pool already running.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
_pool_size: int | None = None


def submit_erp_run(desired_workers: int, fn: Callable[[], None]) -> None:
    """Queue fn to run on the shared ERP pool. At most `desired_workers` run at once; anything
    beyond that waits in the pool's own internal queue, in submission order.

    If the configured worker count has changed since the last call, the old pool is retired
    (shutdown(wait=False) - whatever it already started keeps running to completion, it just
    stops accepting new work) and a freshly-sized one takes over new submissions."""
    global _pool, _pool_size
    with _lock:
        if _pool is None or _pool_size != desired_workers:
            if _pool is not None:
                _pool.shutdown(wait=False)
            _pool = ThreadPoolExecutor(max_workers=desired_workers, thread_name_prefix="erp-submit")
            _pool_size = desired_workers
        _pool.submit(fn)
