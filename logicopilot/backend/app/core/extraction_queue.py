"""At most a handful of document extractions - and classifications - run at once.

Extraction was the one background path in this codebase with no limit on it: every job being
read started its own raw thread, however many that came to. Each of those threads holds a
DATABASE connection for its whole run - and not because its queries are slow. They take
milliseconds. The session keeps its transaction open while the thread waits on OpenAI, which
is thirty-odd seconds per call and many calls per job, so the connection sits there
`idle in transaction`: occupied, doing nothing, for minutes at a time.

On 2026-11-03 production reached 28 connections in exactly that state against a pool of 40,
and the site stopped answering. Ordinary page loads could not get a connection, and
`get_current_user` failing to get one surfaced to operators as being logged out - a Jobs list
that spun forever and a 401 behind it. Nothing was broken and nothing was overloaded; the
database was idle. The connections were simply all spoken for.

Two things made a latent problem into an outage on that day. Reading every page of a document
instead of only the first six (5c037a9) multiplied how long each thread holds its connection,
and the email poller creates jobs on its own, so a batch of mail starts many extractions at
once with nobody touching the system.

Classification (deciding what a freshly-uploaded or freshly-mailed file even IS, before
extraction ever starts) makes the exact same shape of OpenAI call and was found holding the
exact same kind of connection open the same day, just never through this queue - Smart
Upload's own classification call runs straight in the request that uploaded it, and the email
poller's runs straight in its own per-message worker thread, both uncapped. With the backend
running as a single process (see deploy notes), enough of these running at once starve not
just the database pool but the process's own request-handling threads: a handful of slow
uploads/emails was enough, on its own, to make an unrelated `GET /jobs/{id}` time out at
nginx's 60s limit with nothing wrong in the database at all. `run_queued` below is for exactly
this - the caller already needs the answer before it can respond, so unlike `submit_extraction`
it blocks for a result, but it shares the SAME worker pool and the SAME cap, so "six running at
once" counts classification and extraction together, not six of each.

ERP submissions have had this treatment since they were written - see erp_queue.submit_erp_run.
This is the same idea for extraction (and now classification), written deliberately alike so
the three read as one pattern rather than several.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Six at once, by default.
#
# The connection pool allows 40, and the WEBSITE has to be able to get one of them at any
# moment - that is the thing an operator notices immediately. So extraction is given a small
# fixed share rather than "whatever happens to be left", which is how it came to take all of
# them. Six leaves 34 for everything else, which is more headroom than the site has ever
# needed.
#
# Six is also comfortably inside the OpenAI rate limits these keys carry. An unbounded burst
# was not, so this quietly removes a second failure mode as well.
DEFAULT_EXTRACTION_WORKERS = 6

# Tunable on the box without a deploy (EXTRACTION_WORKERS=10), because the right number
# depends on how many connections that server's pool has and what else shares it. Clamped at
# both ends: 0 would stop extraction happening at all, and anything approaching the pool size
# puts us back where we started.
_MAX_SANE_WORKERS = 20


def _worker_count() -> int:
    raw = os.getenv("EXTRACTION_WORKERS", "")
    if not raw.strip():
        return DEFAULT_EXTRACTION_WORKERS
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "EXTRACTION_WORKERS=%r is not a number - using %d", raw, DEFAULT_EXTRACTION_WORKERS
        )
        return DEFAULT_EXTRACTION_WORKERS
    return max(1, min(n, _MAX_SANE_WORKERS))


_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def _get_pool() -> ThreadPoolExecutor:
    """Sized once, on first use. Unlike the ERP pool there is no per-call worker count to
    honour, so there is nothing to resize against and no reason to retire a live pool."""
    global _pool
    with _lock:
        if _pool is None:
            workers = _worker_count()
            _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract")
            logger.info("extraction pool started with %d worker(s)", workers)
        return _pool


def submit_extraction(fn: Callable[[], None]) -> None:
    """Queue fn to run on the shared pool, without waiting for it to finish.

    At most `_worker_count()` run at once (shared with run_queued below); anything beyond
    that waits in the pool's own internal queue, in submission order.

    A job that waits is NOT lost, failed, or skipped - it starts a little later. Its row stays
    "extracting" in the meantime, which is already what the screen shows while extraction
    runs, so a queued job and a running one look the same to an operator. That is the whole
    trade: under a burst, the last job in is slower to start, and the site stays up for
    everyone instead of going down for everyone.
    """
    _get_pool().submit(fn)


def run_queued(fn: Callable[[], T]) -> T:
    """Run fn on the SAME shared pool as submit_extraction, and block for its result.

    For callers that cannot just fire-and-forget - Smart Upload and the email poller both
    need classification's actual answer (which slot each file landed in, what came back
    unclassified) before they can respond or carry on - so this waits, where
    submit_extraction's background reads do not. It still shares one pool and one cap with
    extraction: six running at once counts the two kinds of work together, not six of each,
    which is what actually keeps a connection/thread burst from recreating 2026-11-03's outage
    no matter which of the two kinds of call it is made of.
    """
    return _get_pool().submit(fn).result()
