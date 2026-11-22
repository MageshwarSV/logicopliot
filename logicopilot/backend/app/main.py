import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.db.base  # noqa: F401  (registers all models with the ORM mapper before first query)
from app.api.v1 import api_router
from app.core.config import get_settings

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _configure_logging(level_name: str) -> None:
    """Give this application's own loggers somewhere to go.

    Nothing configured logging before, so the root logger sat at its default WARNING with no
    handler - which means every logger.info() in the codebase was thrown away. Not a cosmetic
    problem: "which slot did the analyser pick for this attachment", "the email poller created
    N jobs", "attached this file to that control" were all being written and all being
    discarded, so a document that went unrouted left no trace to diagnose it by. uvicorn
    configures its own loggers and leaves the root alone, which is why only warnings ever
    reached pm2.
    """
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        root.addHandler(handler)
    # These two are chatty enough to bury everything else at INFO, and neither says anything
    # this application needs.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(getattr(settings, "log_level", "INFO"))
    app = FastAPI(title="Cargora API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_custom_header_on_mutations(request: Request, call_next):
        """Cheap CSRF mitigation alongside SameSite=Lax cookies: a cross-site form POST
        cannot set custom headers, so a same-origin XHR/fetch header is required here."""
        # The one exception: Razorpay's server-to-server webhook cannot send the header. It
        # uses no cookie and is authenticated by its signature instead (app/api/v1/payments.py).
        if (request.method in MUTATING_METHODS and "x-requested-with" not in request.headers
                and request.url.path != "/api/v1/payments/razorpay/webhook"):
            return JSONResponse(status_code=403, content={"detail": "Missing required header"})
        return await call_next(request)

    app.include_router(api_router)

    @app.on_event("startup")
    async def _raise_request_thread_ceiling() -> None:
        """Every plain `def` endpoint (almost all of them here) runs on a shared pool of
        worker threads AnyIO manages for the whole process, capped at 40 by default. That cap
        is unrelated to the database pool - it is how many requests, of ANY kind, this one
        process can be in the middle of handling at once.

        Found live on 2026-11-03: a handful of slow Smart-Upload/email classifications (see
        extraction_queue.py) was enough, on its own, to fill that default 40-thread ceiling,
        and a plain `GET /jobs/{id}` - doing nothing slow itself - queued behind them for a
        free thread until nginx gave up at 60s. The database was fine; there was simply
        nowhere left to even START running a fast request. This backend runs as a single
        process (see deploy notes), so there is no second worker for a fast request to fall
        back to the way there would be with --workers > 1.

        Raised well above what classification+extraction's own cap (six at a time, shared,
        see extraction_queue.py) could ever occupy, so a fast, cheap request always has a free
        thread to start on regardless of how many slow ones are already queued or running.
        """
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = 200

    @app.on_event("startup")
    def _release_stranded_runs() -> None:
        """Free any job left saying `processing` or `extracting` by the previous process.

        An ERP entry, and now extraction too, run in a thread of THIS process, so nothing that
        was running when the server stopped is running now - a deploy, a pm2 restart or a
        crash kills it mid-run. The job's status was set before the work began and is only
        rewritten when it ends, so those jobs said Running (or AI Processing) for ever: they
        could not be re-run, could not be completed, and no screen ever admitted they were
        dead. One had been stuck for nearly six hours before anybody could say why.

        An ERP run in that state (the direct Submit Entry path, which ran inside the operator's
        own request) is marked failed, which is the truth and is re-runnable, with the reason
        spelled out so nobody mistakes it for the ERP rejecting the entry. Only where ERP
        entries actually run (PROCESS_ROLE all or erp): restarting a split WEB server must not
        fail entries the ERP server is still in the middle of.

        A stranded extraction is NOT touched here any more: it stays `extracting` and is read
        again, oldest first, by app/core/restart_resume.py (started below, in the process that
        owns background work) - `draft` only looked recoverable; nothing ever picked it back up.
        """
        from app.core.process_role import runs, startup_report

        startup_report()
        if not runs("erp"):
            return
        from app.db.session import SessionLocal
        from app.models.job import Job

        db = SessionLocal()
        try:
            stuck =db.query(Job).filter(Job.status == "processing").all()
            for job in stuck:
                job.status = "failed"
                job.erp_status = "stopped"
                job.erp_reason = ("The server restarted while this entry was in progress, so "
                                  "the run was lost. Nothing was submitted by it - check the "
                                  "ERP before re-running.")
            if stuck:
                db.commit()
                logging.getLogger(__name__).warning(
                    "released %s job(s) left mid-entry by the previous process: %s",
                    len(stuck), ", ".join(j.reference for j in stuck))
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("could not release stranded job runs")
            db.rollback()
        finally:
            db.close()

    @app.on_event("startup")
    def _apply_openai_key_override() -> None:
        """A Super Admin's own OpenAI key, set from Settings, survives a restart - put it
        into effect before anything else on startup tries to make an AI call."""
        from app.core.system_settings import apply_openai_api_key_override
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            apply_openai_api_key_override(db)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("could not apply the stored OpenAI key override")
        finally:
            db.close()

    @app.on_event("startup")
    def _load_openai_key_pool() -> None:
        """The pooled keys (if any) a Super Admin has added from Settings - loaded into this
        process's own memory once up front, the same reason the single key above is applied
        at startup rather than read fresh per call. See app/core/openai_pool.py."""
        from app.core.openai_pool import refresh_pool, refresh_tenant_modes
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            refresh_pool(db)
            refresh_tenant_modes(db)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("could not load the OpenAI key pool")
        finally:
            db.close()
        # Split servers: a key saved on another server is picked up here too (settings_sync.py).
        from app.core.process_role import is_split

        if is_split():
            from app.core import settings_sync

            settings_sync.start()

    @app.on_event("startup")
    def _start_email_poller() -> None:
        # Automatic inbox polling (no-op unless EMAIL_POLL_MINUTES > 0 and creds set).
        # Uvicorn's --workers forks several of this whole process; without the singleton
        # lock every worker would poll the same mailboxes on its own schedule, multiplying
        # both the IMAP traffic and the risk of two workers racing on the same message.
        from app.core.process_role import runs
        from app.core.worker_lock import am_i_the_singleton_worker

        if not runs("mail") or not am_i_the_singleton_worker():
            return
        from app.core.email_puller import release_interrupted_claims, start_scheduler

        # Before the first poll: mails the previous process was in the middle of reading are
        # handed back, so they are fetched and read again - whatever day they arrived.
        # Only where mail is actually polled (SIT and production) - never in tests or local dev.
        _db = None
        try:
            from app.core.config import get_settings as _gs0

            if _gs0().email_poll_minutes <= 0:
                raise LookupError("mail polling is off here")
            from app.db.session import SessionLocal as _SessionLocal

            _db = _SessionLocal()
            released = release_interrupted_claims(_db)
            if released:
                logging.getLogger(__name__).warning(
                    "restart: %d mail(s) interrupted mid-read will be read again: %s",
                    len(released), "; ".join(released))
        except LookupError:
            pass
        except Exception:  # noqa: BLE001 - must never stop mail polling from starting
            logging.getLogger(__name__).exception("could not release interrupted mail claims")
            if _db is not None:
                _db.rollback()
        finally:
            if _db is not None:
                _db.close()

        start_scheduler()
        # The large-job lane (app/core/large_job_lane.py) - also picks up extractions queued on
        # it before a restart. Only where mail is polled at all: that is the only thing that
        # ever puts work on it.
        from app.core.config import get_settings as _gs

        if _gs().email_poll_minutes > 0:
            from app.core.large_job_lane import start as start_large_job_lane

            start_large_job_lane()

    @app.on_event("startup")
    def _resume_parked_sessions() -> None:
        """Bring back any browser that should be parked at a script's checkpoint.

        This is what makes the feature survive a pm2 restart, a deploy or a reboot: the
        Super Admin sets "stay open" once and the session re-establishes itself, rather than
        someone having to notice it is gone. No-op unless a script has both a checkpoint and
        stay_open, so a normal install starts nothing.

        Gated the same way as the email poller: with multiple uvicorn workers, only the
        single lock-holding process may drive these browsers - two workers resuming the same
        parked script would mean two browsers replaying the same ERP entry.
        """
        from app.core.process_role import runs
        from app.core.worker_lock import am_i_the_singleton_worker

        if not runs("erp") or not am_i_the_singleton_worker():
            return
        import threading

        def _go() -> None:
            from app.core.parked import resume_all
            from app.db.session import SessionLocal

            resume_all(SessionLocal)

        # Off the startup path: launching Chromium must never delay the app accepting traffic.
        threading.Thread(target=_go, name="park-resume", daemon=True).start()

    @app.on_event("startup")
    def _start_erp_queue_workers() -> None:
        """Pick up ERP submissions left queued by the process that went away, and start the
        workers that drain them (app/core/erp_redis_queue.py).

        No-op without a configured, reachable Redis - the in-process pool then handles
        everything exactly as it did before, backlog and all.

        Gated like the email poller and the parked sessions: with several uvicorn workers
        only the lock-holding process runs these, so the number of ERP runs actually in
        flight stays the configured worker count rather than that many PER process.

        Split servers (PROCESS_ROLE): the ERP server starts the queue and branch logins and
        resumes ERP runs; the worker server resumes interrupted extractions and then reads the
        ones the website hands it (app/core/extraction_dispatch.py).
        """
        from app.core.process_role import is_split, runs
        from app.core.worker_lock import am_i_the_singleton_worker

        kinds = tuple(k for k, part in (("extraction", "extraction"), ("ERP", "erp"))
                      if runs(part))
        if not kinds or not am_i_the_singleton_worker():
            return
        doorbell = is_split() and "extraction" in kinds
        import threading

        def _go() -> None:
            from app.core.restart_resume import resume_interrupted_work

            if doorbell:
                from app.core import extraction_dispatch

                # Everything waiting is `extracting` in the database: the resume below reads it.
                extraction_dispatch.clear_queue()

            if "ERP" in kinds:
                from app.core.erp_redis_queue import start
                from app.core.system_settings import get_erp_script_workers
                from app.db.session import SessionLocal

                db = SessionLocal()
                try:
                    start(get_erp_script_workers(db))
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception("could not start the ERP queue")
                finally:
                    db.close()
                # The always-on branch logins (app/core/branch_sessions.py) - before the backlog
                # comes back, so branch jobs find their login coming up rather than missing.
                try:
                    from app.core import branch_sessions

                    branch_sessions.start()
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception("could not start the branch logins")
            # Then everything the previous process was in the middle of - extractions and ERP
            # runs - back in its queue, oldest first. After start(), so Redis has already put
            # back its own reserved items and nothing is queued twice.
            resume_interrupted_work(kinds)
            if doorbell:
                extraction_dispatch.start()

        # Off the startup path: a Redis that is slow to answer must not hold up the app.
        threading.Thread(target=_go, name="erp-queue-start", daemon=True).start()

    @app.on_event("startup")
    def _start_pipeline() -> None:
        """Phase 2 pipeline workers (app/pipeline/). Off unless PIPELINE_V2 is set AND Redis
        answers - then nothing starts and the app is exactly as before. Same singleton gate as
        the mail cycle and the ERP queue, so items are worked by one process only."""
        from app.core.process_role import runs
        from app.core.worker_lock import am_i_the_singleton_worker

        if not runs("pipeline") or not am_i_the_singleton_worker():
            return
        import threading

        def _go() -> None:
            try:
                from app.core.config import get_settings as _gs
                from app.pipeline import runner

                if runner.enabled():
                    runner.start(_gs().pipeline_threads_per_stage)
            except Exception:  # noqa: BLE001 - must never stop the app starting
                logging.getLogger(__name__).exception("could not start the pipeline")

        threading.Thread(target=_go, name="pipeline-start", daemon=True).start()

    @app.on_event("shutdown")
    def _stop_parked_sessions() -> None:
        """Close parked browsers on the way down, so a restart cannot leak a Chromium."""
        from app.core.erp_redis_queue import stop as stop_erp_queue
        from app.core.parked import MANAGER

        stop_erp_queue()
        MANAGER.unpark_all()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    # Lets you run this file directly (`uv run python -m app.main`) instead of typing out
    # the full `uvicorn app.main:app --reload --port 8000` command every time.
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=30299, reload=True)
