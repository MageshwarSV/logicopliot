"""Which background work this process starts - so the app can run split over several servers
(website, workers, ERP) from one codebase, with only a setting telling them apart.

PROCESS_ROLE in .env:
    all     everything in one process - the default, and exactly how every server ran before
    web     the website and API only; no background work at all
    worker  mail polling (+ the large-job lane), extractions started from the website (handed
            over through Redis) and resuming interrupted ones, the pipeline
    erp     the ERP queue, branch logins, parked browsers, resuming interrupted ERP runs, and
            failing any direct ERP entry the previous ERP process left half-done

Each background part is started by exactly one role, so two servers never poll the same
mailbox or drive the same ERP entry. A value that is not one of the four starts NO background
work and says so in the log: a typo must never make a second server read customer mail.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ROLES = ("all", "web", "worker", "erp")

# part -> the split role that owns it ("all" owns every part)
PART_OWNER = {
    "mail": "worker",               # IMAP poller, interrupted mail claims, large-job lane
    "pipeline": "worker",           # Phase 2 stage workers
    "extraction": "worker",        # website Run / Re-run extraction (via Redis) + restart resume
    "erp": "erp",                   # ERP queue, branch logins, parked browsers, ERP resume
}

_warned: set[str] = set()


def role() -> str:
    from app.core.config import get_settings

    value = (getattr(get_settings(), "process_role", "all") or "all").strip().lower()
    if value in ROLES:
        return value
    if value not in _warned:
        _warned.add(value)
        logger.error("PROCESS_ROLE=%r is not one of %s - starting NO background work",
                     value, "/".join(ROLES))
    return "invalid"


def is_split() -> bool:
    return role() != "all"


def runs(part: str) -> bool:
    """True when this process should start that background part."""
    r = role()
    return r == "all" or PART_OWNER.get(part) == r


def lock_name() -> str:
    """The singleton lock file for this process. One per role once split, so a web, a worker
    and an ERP process started from the same checkout (one machine) don't block each other -
    only two processes of the SAME role do."""
    r = role()
    return "singleton_worker.lock" if r == "all" else f"singleton_worker-{r}.lock"


def startup_report() -> None:
    """One line at startup saying what this process runs, plus a warning for a split without
    Redis (Submit Entry / Final Approve queued on one server could not reach the ERP server)."""
    from app.core.config import get_settings

    r = role()
    if r == "all":
        return
    parts = [p for p in PART_OWNER if runs(p)]
    logger.warning("PROCESS_ROLE=%s: this process starts %s", r, ", ".join(parts) or "no background work")
    if not (get_settings().redis_url or "").strip():
        logger.error("PROCESS_ROLE=%s without REDIS_URL: ERP work queued here stays in THIS "
                     "process - set REDIS_URL on every server of a split", r)
