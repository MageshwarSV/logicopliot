"""gunicorn settings for the website / API process, with load-based worker scaling.

Production (2026-11-26): the app server ran `uvicorn --workers 1` - one Python process serving
every user on an 8-CPU machine. The jobs menu, the jobs list and the bell are polled every few
seconds by every open tab (about 5 requests a second all day), each costing 0.5-2 s of work, so
that one process sat at ~100% of a core and every request - even a 0 KB one - queued behind the
others for 1-5 s.

Start:  venv/bin/gunicorn -c gunicorn.conf.py app.main:app

Workers:
  * PROCESS_ROLE=web (the split app server: website + API, no background work): starts with
    WEB_WORKERS_MIN (4) and scales with load up to WEB_WORKERS_MAX (8) - see _autoscale.
  * any other role (all / worker / erp): exactly ONE worker, no scaling. Those processes run the
    mail poller, the ERP queue, the pipeline and the schedulers, which must never run twice.
"""
import os
import signal
import threading
import time

ROLE = (os.environ.get("PROCESS_ROLE") or "").strip().lower()
if not ROLE:
    # The app reads PROCESS_ROLE from .env (pydantic settings); gunicorn reads this file before
    # the app is imported, so read the same .env line here.
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("PROCESS_ROLE="):
                    ROLE = line.split("=", 1)[1].strip().strip("'\"").lower()
    except OSError:
        pass
ROLE = ROLE or "all"
SCALES = ROLE == "web"

MIN_WORKERS = max(1, int(os.environ.get("WEB_WORKERS_MIN", "4"))) if SCALES else 1
MAX_WORKERS = max(MIN_WORKERS, int(os.environ.get("WEB_WORKERS_MAX", "8"))) if SCALES else 1

if SCALES:
    # Each worker has its own database pool (app/db/session.py). Up to 8 workers x 20 = 160
    # connections from this server at the very most - Postgres' max_connections must allow it
    # (300 on production's db server, with the ai and erp servers' own 60 each).
    os.environ.setdefault("DB_POOL_SIZE", "10")
    os.environ.setdefault("DB_MAX_OVERFLOW", "10")

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:30299")
workers = MIN_WORKERS
worker_class = "uvicorn_worker.UvicornWorker"
# A worker that stops answering gunicorn's heartbeat for this long is restarted. Requests
# themselves are not cut by it (uvicorn workers run them on their own loop / threads).
timeout = 180
graceful_timeout = 60
keepalive = 5
# nginx on this machine is the only client; trust its X-Forwarded-* headers, as uvicorn did.
forwarded_allow_ips = "127.0.0.1"
accesslog = None
errorlog = "-"
loglevel = "info"

# ---- load-based scaling ------------------------------------------------------------------------
CHECK_EVERY = 15            # seconds between looks
UP_BUSY = 60.0              # average worker CPU % that counts as busy
UP_AFTER = 2                # busy looks in a row before adding a worker (30 s)
DOWN_IDLE = 15.0            # average worker CPU % that counts as quiet
DOWN_AFTER = 20             # quiet looks in a row before removing a worker (5 min)
COOLDOWN = 60               # seconds after a change before the next one

_TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _cpu_ticks(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])        # utime + stime
    except (OSError, IndexError, ValueError):
        return None


def _autoscale(server) -> None:
    """Add a worker (SIGTTIN) while the workers are busy, remove one (SIGTTOU) after a quiet
    spell - always between MIN_WORKERS and MAX_WORKERS. Runs in the gunicorn master."""
    last: dict[int, int] = {}
    busy = quiet = 0
    changed_at = 0.0
    while True:
        time.sleep(CHECK_EVERY)
        try:
            pids = list(server.WORKERS.keys())
            loads = []
            for pid in pids:
                ticks = _cpu_ticks(pid)
                if ticks is None:
                    continue
                if pid in last:
                    loads.append(100.0 * (ticks - last[pid]) / _TICK / CHECK_EVERY)
                last[pid] = ticks
            for gone in set(last) - set(pids):
                last.pop(gone, None)
            if not loads:
                continue
            avg = sum(loads) / len(loads)
            count = len(pids)
            busy = busy + 1 if avg >= UP_BUSY else 0
            quiet = quiet + 1 if avg <= DOWN_IDLE else 0
            if time.time() - changed_at < COOLDOWN:
                continue
            if busy >= UP_AFTER and count < MAX_WORKERS:
                server.log.info("autoscale: workers busy (avg %.0f%% CPU over %d) - adding one (%d -> %d)",
                                avg, count, count, count + 1)
                os.kill(os.getpid(), signal.SIGTTIN)
                changed_at, busy = time.time(), 0
            elif quiet >= DOWN_AFTER and count > MIN_WORKERS:
                server.log.info("autoscale: workers quiet (avg %.0f%% CPU over %d) - removing one (%d -> %d)",
                                avg, count, count, count - 1)
                os.kill(os.getpid(), signal.SIGTTOU)
                changed_at, quiet = time.time(), 0
        except Exception as exc:  # noqa: BLE001 - scaling must never take the server down
            server.log.warning("autoscale: skipped a check (%s)", exc)


def when_ready(server):
    server.log.info("PROCESS_ROLE=%s: %d worker(s)%s", ROLE, MIN_WORKERS,
                    f", scaling up to {MAX_WORKERS} with load" if SCALES and MAX_WORKERS > MIN_WORKERS
                    else " (fixed - this role runs background work that must not run twice)")
    if SCALES and MAX_WORKERS > MIN_WORKERS:
        threading.Thread(target=_autoscale, args=(server,), name="autoscale", daemon=True).start()
