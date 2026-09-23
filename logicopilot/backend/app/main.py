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
        if request.method in MUTATING_METHODS and "x-requested-with" not in request.headers:
            return JSONResponse(status_code=403, content={"detail": "Missing required header"})
        return await call_next(request)

    app.include_router(api_router)

    @app.on_event("startup")
    def _release_stranded_runs() -> None:
        """Free any job left saying `processing` or `extracting` by the previous process.

        An ERP entry, and now extraction too, run in a thread of THIS process, so nothing that
        was running when the server stopped is running now - a deploy, a pm2 restart or a
        crash kills it mid-run. The job's status was set before the work began and is only
        rewritten when it ends, so those jobs said Running (or AI Processing) for ever: they
        could not be re-run, could not be completed, and no screen ever admitted they were
        dead. One had been stuck for nearly six hours before anybody could say why.

        An ERP run in that state is marked failed, which is the truth and is re-runnable, with
        the reason spelled out so nobody mistakes it for the ERP rejecting the entry. A stranded
        extraction is put back to `draft` instead - nothing was submitted anywhere by it, and
        `draft` is what lets the auto-trigger simply pick it back up rather than requiring the
        operator to notice and press Extract themselves.
        """
        from app.db.session import SessionLocal
        from app.models.job import Job

        db = SessionLocal()
        try:
            stuck = db.query(Job).filter(Job.status == "processing").all()
            for job in stuck:
                job.status = "failed"
                job.erp_status = "stopped"
                job.erp_reason = ("The server restarted while this entry was in progress, so "
                                  "the run was lost. Nothing was submitted by it - check the "
                                  "ERP before re-running.")
            stuck_extracting = db.query(Job).filter(Job.status == "extracting").all()
            for job in stuck_extracting:
                job.status = "draft"
            stuck = stuck + stuck_extracting
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
    def _start_email_poller() -> None:
        # Automatic inbox polling (no-op unless EMAIL_POLL_MINUTES > 0 and creds set).
        # Uvicorn's --workers forks several of this whole process; without the singleton
        # lock every worker would poll the same mailboxes on its own schedule, multiplying
        # both the IMAP traffic and the risk of two workers racing on the same message.
        from app.core.worker_lock import am_i_the_singleton_worker

        if not am_i_the_singleton_worker():
            return
        from app.core.email_puller import start_scheduler

        start_scheduler()

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
        from app.core.worker_lock import am_i_the_singleton_worker

        if not am_i_the_singleton_worker():
            return
        import threading

        def _go() -> None:
            from app.core.parked import resume_all
            from app.db.session import SessionLocal

            resume_all(SessionLocal)

        # Off the startup path: launching Chromium must never delay the app accepting traffic.
        threading.Thread(target=_go, name="park-resume", daemon=True).start()

    @app.on_event("shutdown")
    def _stop_parked_sessions() -> None:
        """Close parked browsers on the way down, so a restart cannot leak a Chromium."""
        from app.core.parked import MANAGER

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
