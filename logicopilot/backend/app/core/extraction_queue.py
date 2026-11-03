"""At most a handful of document extractions run at once.

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

ERP submissions have had this treatment since they were written - see erp_queue.submit_erp_run.
This is the same idea for extraction, written deliberately alike so the two read as one
pattern rather than two.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

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


def submit_extraction(fn: Callable[[], None]) -> None:
    """Queue fn to run on the shared extraction pool.

    At most `_worker_count()` run at once; anything beyond that waits in the pool's own
    internal queue, in submission order.

    A job that waits is NOT lost, failed, or skipped - it starts a little later. Its row stays
    "extracting" in the meantime, which is already what the screen shows while extraction
    runs, so a queued job and a running one look the same to an operator. That is the whole
    trade: under a burst, the last job in is slower to start, and the site stays up for
    everyone instead of going down for everyone.

    Sized once, on first use. Unlike the ERP pool there is no per-call worker count to honour,
    so there is nothing to resize against and no reason to retire a live pool.
    """
    global _pool
    with _lock:
        if _pool is None:
            workers = _worker_count()
            _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract")
            logger.info("extraction pool started with %d worker(s)", workers)
        pool = _pool
    pool.submit(fn)
